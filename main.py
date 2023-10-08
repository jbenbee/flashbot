import json
import os.path
import subprocess
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from signal import signal, SIGINT
import re

import joblib
import numpy as np
import pandas as pd
from flask import Flask
from flask import request
from waitress import serve
import requests
import argparse

import openai

from learning_plan import LearningPlan
from words_progress_db import WordsProgressDB
from reading_db import ReadingDB
from reading_exercise import ReadingExercise
from user_config import UserConfig
from words_db import WordsDB
from words_exercise import WordsExerciseLearn, WordsExerciseTest


app = Flask(__name__)


def get_assistant_response(query, tokens):
    if client != 'openai':
        raise ValueError(f'Unknown client {client}')

    nattempts = 0
    messages = [
                {"role": "system", "content": f"You are a helpful assistant called {str(uuid.uuid1())[:5]}. Follow precisely the instructions of the user and pretend to be a human."},
                {"role": "user", "content": query},
            ]
    while nattempts < 3:
        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=tokens
            )
            break
        except openai.error.OpenAIError:
            time.sleep(nattempts + 1)

    if nattempts == 3:
        print('The assistant raised an error.')
        text = 'Sorry, the assistant raised some error while processing this request.'
    else:
        try:
            text = response['choices'][0]['message']['content']
        except Exception as e:
            print(response)
            text = f'Something is wrong {e}. Response: {response}'

    mes = text.strip()
    return mes


def handle_new_exercise(chat_id, exercise):

    tokens = user_config.get_user_data(chat_id)['max_tokens']
    running_exercises[chat_id] = exercise
    if isinstance(exercise, WordsExerciseLearn):
        increment_word_reps(chat_id, exercise.word_id)
        words_progress_db.save_progress()
    next_query, is_last_query = exercise.get_next_assistant_query(user_response=None)
    assistant_response = get_assistant_response(next_query, tokens)
    responded_correctly = False  # True if the assistant responded in correct format
    nattempts = 0
    max_attempts = 5
    while not responded_correctly and nattempts < max_attempts:
        nattempts += 1
        try:
            message = exercise.get_next_message_to_user(next_query, assistant_response)
            responded_correctly = True
        except ValueError:
            print(f'Wrong response: {assistant_response}\nRe-requesting...')
            assistant_response = get_assistant_response(next_query, tokens)

    if nattempts == max_attempts and not responded_correctly:
        message = 'Sorry, the assistant raised some error while processing this request. Please retry.'

    buttons = None
    if isinstance(exercise, WordsExerciseLearn):
        buttons = [(exercise.uid, 'Ignore this word'), (exercise.uid, 'I know this word')]
    if isinstance(exercise, WordsExerciseTest):
        buttons = [(exercise.uid, 'Hint'), (exercise.uid, 'Correct answer')]
    tel_send_message(chat_id, message, buttons=buttons)


def ping_user(chat_id, lang, exercise_type, exercise_data):
    lock.acquire()
    if exercise_type == 'reading':
        exercise = lp.get_next_reading_exercise(chat_id, lang, topics=exercise_data)
    elif exercise_type == 'words':
        exercise = lp.get_next_words_exercise(chat_id, lang, mode=exercise_data)
    else:
        raise ValueError(f'Unknown exercise type {exercise_type}')
    handle_new_exercise(chat_id, exercise)
    lock.release()


def parse_message(message):
    chat_id = None
    data = None
    type = None

    if 'message' in message.keys() and 'entities' in message['message'].keys() \
            and message['message']['entities'][0]['type'] == 'bot_command':
        type = 'command'
        chat_id = message['message']['chat']['id']
        data = message['message']['text']
    elif 'message' in message.keys():
        type = 'message'
        chat_id = message['message']['chat']['id']
        data = message['message']['text']
    elif 'callback_query' in message.keys():
        type = 'button'
        chat_id = message['callback_query']['message']['chat']['id']
        data = message['callback_query']['data']
    return chat_id, type, data


def tel_send_message(chat_id, text, buttons=None):
    url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': text
    }

    # determine max line length to know if to display buttons on separate lines or on the same one
    lines = text.split('\n')
    max_len = max([len(line) for line in lines])

    if buttons is not None:
        buttons_list = []
        for button_id, button_text in buttons:
            buttons_list.append(
                {
                    "text": button_text,
                    "callback_data": f"{button_text}_{button_id}" if button_id is not None else button_text
                }
            )

        buttons_to_send = [[button] for button in buttons_list] if max_len < 74 else [buttons_list]
        payload['reply_markup'] = {
            "inline_keyboard": buttons_to_send
        }
    r = requests.post(url, json=payload)
    return r


def handle_commands(chat_id, lang, command):
        # user pressed a command
        print(f'Received a command: {command}')

        if command == '/next_test':
            tel_send_message(chat_id, f'Thinking...')
            exercise = lp.get_next_words_exercise(chat_id, lang, 'test')
            if exercise is None:
                tel_send_message(chat_id, 'There are no words for testing.')
            else:
                handle_new_exercise(chat_id, exercise)
        elif command == '/next_new':
            tel_send_message(chat_id, f'Thinking...')
            exercise = lp.get_next_words_exercise(chat_id, lang, 'learn')
            if exercise is None:
                tel_send_message(chat_id, 'There are no new words to learn.')
            else:
                handle_new_exercise(chat_id, exercise)
        elif command == '/next_reading':
            tel_send_message(chat_id, f'Thinking...')
            exercise = lp.get_next_reading_exercise(chat_id, lang)
            if exercise is None:
                tel_send_message(chat_id, 'Sorry, there are no reading topics for you.')
            else:
                handle_new_exercise(chat_id, exercise)
        elif command == '/add_word':
            running_commands[chat_id] = command
            tel_send_message(chat_id, 'Type the word that you would like to add.')
        elif command == '/known_words':
            tel_send_message(chat_id, f'Thinking...')
            known_words = get_known_words(chat_id, lang)
            known_words_str = "\n".join(known_words)
            tel_send_message(chat_id, f'Number of learned words: {len(known_words)}\n'
                                      f'List of learned words:\n{known_words_str}')
        elif command == '/cur_word_group':
            cur_word_group = user_config.get_user_data(chat_id)['current_word_group']
            group_info = get_word_group_info(chat_id, lang, cur_word_group)
            tel_send_message(chat_id, f'Current word group is {cur_word_group}.\n'
                                      f'Group info:\n'
                                      f'{group_info}')
        elif command == '/sel_word_group':
            running_commands[chat_id] = command
            buttons = [(None, group) for group in user_config.get_user_data(chat_id)['word_groups']]
            tel_send_message(chat_id, 'Select a word group:', buttons=buttons)
        else:
            raise ValueError(f'Unknown command {command}')


def handle_button_press(chat_id, lang, udata, exercise):
    print(f'Received a button: {udata}')
    button_id = udata.split('_')[-1]
    if button_id != exercise.uid:
        tel_send_message(chat_id, 'Sorry, the exercise has been completed or is expired.')
        print(f'Button id {button_id} does not match exercise id {exercise.uid}')
    else:
        if isinstance(exercise, WordsExerciseLearn) or isinstance(exercise, WordsExerciseTest):
            if f'Ignore this word_{exercise.uid}' == udata:
                words_progress_db.ignore_word(chat_id, exercise.word_id)
                words_progress_db.save_progress()
                tel_send_message(chat_id, f'The word "{exercise.word}" is added to ignore list.')
            elif f'Hint_{exercise.uid}' == udata:
                tel_send_message(chat_id, f'Try to use the word "{exercise.word}" in your translation.')
            elif f'Correct answer_{exercise.uid}' == udata:
                tel_send_message(chat_id, exercise.correct_answer())
            elif f'I know this word_{exercise.uid}' == udata:
                words_progress_db.add_known_word(chat_id, exercise.word_id)
                words_progress_db.save_progress()
                tel_send_message(chat_id, f'The word "{exercise.word}" is added to the list of known words.')

                known_words = get_known_words(chat_id, lang)
                n_known_words = len(known_words)
                if n_known_words % 5 == 0:
                    tel_send_message(chat_id, f'Congrats, you already learned {n_known_words} words!')
            else:
                raise ValueError(f'Unknown callback data {udata}')
        else:
            raise ValueError(f'Unknown exercise type: {type(exercise)}.')


def execute_command(chat_id, lang, command, msg):
    if command == '/add_word':
        # extract word and word group
        word = msg.strip()
        cur_word_group = user_config.get_user_data(chat_id)['current_word_group']
        words_db.add_new_word(word, cur_word_group, lang, chat_id)
        words_db.save_words_db()
        user_msg = f'Word "{word}" is successfully added to word group "{cur_word_group}".'
    elif command == '/sel_word_group':
        cur_word_group = msg
        user_config.set_word_group(chat_id, cur_word_group)
        group_info = get_word_group_info(chat_id, lang, cur_word_group)
        user_msg = f'Selected word group "{msg}".\n'\
                   f'Group info:\n'\
                   f'{group_info}'
    else:
        raise ValueError(f'Unknown command {command}.')
    return user_msg


def handle_user_message(chat_id, lang, tokens, msg):

    if chat_id in running_commands.keys():
        # handle an input for a command
        command = running_commands.pop(chat_id)
        user_msg = execute_command(chat_id, lang, command, msg)
        tel_send_message(chat_id, user_msg)
    elif chat_id in running_exercises.keys():
        exercise = running_exercises.pop(chat_id)

        tel_send_message(chat_id, 'Thinking...')
        next_query, is_last_query = exercise.get_next_assistant_query(user_response=msg)
        assistant_response = get_assistant_response(next_query, tokens)
        buttons = None
        if isinstance(exercise, WordsExerciseLearn):
            buttons = [(exercise.uid, 'Ignore this word'), (exercise.uid, 'I know this word')]
        n_prev_known_words = None
        if isinstance(exercise, WordsExerciseTest):
            n_prev_known_words = len(get_known_words(chat_id, lang))
            increment_word_reps(chat_id, exercise.word_id)

        tel_send_message(chat_id, exercise.get_next_message_to_user(next_query, assistant_response), buttons=buttons)
        words_progress_db.save_progress()

        known_words = get_known_words(chat_id, lang)
        n_known_words = len(known_words)
        if n_known_words % 5 == 0 and n_prev_known_words is not None and n_known_words != n_prev_known_words:
            tel_send_message(chat_id, f'Congrats, you already learned {n_known_words} words!')
    else:
        tel_send_message(chat_id, f'No exercises or commands are running, this message will be ignored: {msg}')
        print(f'No running commands or exercises, ignore user message: {msg}')


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        headers = {k: v for k, v in request.headers.items()}
        if 'X-Telegram-Bot-Api-Secret-Token' not in headers:
            print('No secret token.')
            return 'Error'
        elif headers['X-Telegram-Bot-Api-Secret-Token'] != secret_token:
            print('Secret token does not match')
            return 'Error'
        msg = request.get_json()
        chat_id, type, data = parse_message(msg)
        lang = user_config.get_user_data(chat_id)['language']
        tokens = user_config.get_user_data(chat_id)['max_tokens']
        print(f'Received user message: {data}')

        lock.acquire()
        try:
            if type == 'command':
                if chat_id in running_exercises.keys():
                    # if there were any running exercises, remove them after a command has been pressed
                    running_exercises.pop(chat_id)
                handle_commands(chat_id, lang, data)
            elif type == 'button':
                button_text = data.split('_')[0]
                if button_text in exercise_buttons:
                    # user pressed a button for an exercise
                    if chat_id in running_commands.keys():
                        # if there were any running commands, remove them when the user interacts with an exercise
                        running_commands.pop(chat_id)
                    if chat_id in running_exercises.keys():
                        exercise = running_exercises[chat_id]
                        handle_button_press(chat_id, lang, data, exercise)
                    else:
                        tel_send_message(chat_id, 'Sorry, the exercise has been completed or is expired.')
                else:
                    # user pressed a button required to complete a command
                    if chat_id in running_commands.keys():
                        command = running_commands.pop(chat_id)
                        user_msg = execute_command(chat_id, lang, command, data)
                        tel_send_message(chat_id, user_msg)
                    else:
                        tel_send_message(chat_id, 'Something went wrong, please contact the admin.')
                        raise Exception(f'I think that the user pressed a button for a command, '
                                        f'but there are no running commands for user {chat_id}.')
            elif type == 'message':
                handle_user_message(chat_id, lang, tokens, data)
            else:
                raise ValueError(f'Unknown message type {type}, {data}')
        except Exception as e:
            if chat_id in running_commands.keys(): running_commands.pop(chat_id)
            if chat_id in running_exercises.keys(): running_exercises.pop(chat_id)
            print(e)
            tel_send_message(chat_id, f'Something went terribly wrong, please try again or notify the admin.')
        lock.release()
        return 'ok'
    else:
        return "<h1>Welcome!</h1>"


def exithandler(signal_received, frame, proc):
    # Handle any cleanup here
    print('SIGINT or CTRL-C detected. Exiting gracefully')

    # store running exercises
    joblib.dump(running_exercises, filename=running_exercises_file)

    if proc is not None:
        proc.kill()
    exit(0)


def handle_webhook(local, url):
    x = threading.Thread(target=lambda: webhook_func(local, url), daemon=True)
    x.start()


def start_job_queue(user_data, exercise_type):
    while True:
        jobs = list()
        is_weekend = (datetime.now().weekday() == 5 or datetime.now().weekday() == 6)
        schedule_col = 'weekend' if is_weekend else 'weekday'
        for chat_id, chat_data in user_data.items():
            if chat_data['to_skip']: continue
            user_ping_times = chat_data['ping_schedule'][schedule_col]
            jobs += [{'chat_id': chat_id, 'lang': chat_data['language'], 'time': ping_time, 'exercise_data': exercise_data}
                     for ping_time, exercise_data in user_ping_times.items()]
        jobs = sorted(jobs, key=lambda v: v['time'])
        earliest_time = jobs[0]['time']

        # skip all tasks until now
        jidx = 0
        while jidx < len(jobs) and datetime.now().time() > jobs[jidx]['time']: jidx += 1

        jobs = jobs[jidx:]

        while len(jobs) > 0:
            next_run_time = jobs[0]
            cur_jobs = [item for item in jobs if item['time'] == next_run_time['time']]
            jobs = jobs[len(cur_jobs):]

            for j in cur_jobs:
                print(f"{exercise_type}: {j['chat_id']}, {j['lang']}: {datetime.combine(datetime.today(), next_run_time['time'])}")

            time.sleep((datetime.combine(datetime.today(), next_run_time['time'])-datetime.now()).total_seconds())
            for job in cur_jobs:
                ping_user(chat_id=job['chat_id'], lang=job['lang'],
                          exercise_type=exercise_type, exercise_data=job['exercise_data'])

        tomorrow_ping_time = datetime.combine(datetime.today()+timedelta(days=1), earliest_time)
        print(f'{exercise_type}: Going to sleep until {tomorrow_ping_time}')
        time.sleep((tomorrow_ping_time - datetime.now()).total_seconds())


def handle_job_queue(user_db):

    # words jobs
    user_data = user_db.get_all_user_data()
    for chat_id, v in user_data.items():
        user_data[chat_id]['to_skip'] = ('words' not in user_data[chat_id]['exercise_types'])
        user_data[chat_id]['ping_schedule'] = user_data[chat_id]['schedule']['words']
    x = threading.Thread(target=lambda: start_job_queue(user_data, exercise_type='words'), daemon=True)
    x.start()

    # reading jobs
    user_data = user_db.get_all_user_data()
    for chat_id, v in user_data.items():
        user_data[chat_id]['to_skip'] = ('reading' not in user_data[chat_id]['exercise_types'])
        user_data[chat_id]['ping_schedule'] = user_data[chat_id]['schedule']['reading']
    x = threading.Thread(target=lambda: start_job_queue(user_data, exercise_type='reading'), daemon=True)
    x.start()


def webhook_func(local, url):
    global hookproc
    global secret_token
    sleep_time = 110 * 60 if local else 24 * 60 * 60
    while True:
        if hookproc is not None:
            page = urllib.request.urlopen(f'https://api.telegram.org/bot{bot_token}/setWebhook?remove')
            print(f'Remove webhook status: {page.getcode()}')
            hookproc.kill()
        secret_token = str(uuid.uuid1())
        hookproc = set_webhook(port, secret_token, url)
        time.sleep(sleep_time)  # refresh hook url


def set_webhook(port, secret_token, url):
    if url is not None:
        response = requests.post(
            url=f'https://api.telegram.org/bot{bot_token}/setWebhook',
            data={'url': url, 'secret_token': secret_token}
        ).json()
        response_str = json.dumps(response, indent='\t')
        print(f'Setting webhook status: {response_str}')
        proc = None
    else:
        proc = subprocess.Popen(['ngrok', 'http', f'{port}', '--log=stdout'], stdout=subprocess.PIPE)
        lines_iter = iter(proc.stdout.readline, "")
        found_url = False
        url_regexp = [r"https://[-a-z0-9]+.eu.ngrok.io", r"https://[-a-z0-9]+.ngrok.io", r"https://[-a-z0-9]+.ngrok-free.app"]
        while not found_url:
            stdout_line = str(next(lines_iter))
            url_list = [re.findall(u, stdout_line) for u in url_regexp]
            for ulist in url_list:
                if len(ulist) > 0:
                    found_url = True
                    url = ulist[0]
                    break
        print(f'Webhook url: {url}')
        response = requests.post(
            url=f'https://api.telegram.org/bot{bot_token}/setWebhook',
            data={'url': url, 'secret_token': secret_token}
        ).json()
        response_str = json.dumps(response, indent='\t')
        print(f'Setting webhook status: {response_str}')
    return proc


def get_known_words(chat_id, lang, group=None):
    words_df = words_db.get_words_df()
    progress_df = words_progress_db.get_progress_df()
    progress_words_df = pd.merge(progress_df, words_df, how='left', left_on='word_id',
                                 right_on='id', sort=False, validate='1:1')

    pw_df = progress_words_df.loc[(progress_words_df['chat_id'] == chat_id) &
                                  (progress_words_df['lang'] == lang) &
                                  progress_words_df['is_known']]
    if group is None:
        words = pw_df['word'].to_list()
    else:
        words = pw_df.loc[pw_df['group'] == group, 'word'].to_list()
    return words


def get_word_group_info(chat_id, lang, group):
    user_data = user_config.get_user_data(chat_id)
    wdf = words_db.get_words_df()
    gr_lang_words = wdf.loc[(wdf['lang'] == user_data['language']) & (wdf['group'] == group)].shape[0]
    n_learned_words = len(get_known_words(chat_id, lang, group))
    group_info = f'Total number of words in the group is {gr_lang_words}.\n' \
                 f'Number of learned words in the group is {n_learned_words}.'
    return group_info


def increment_word_reps(chat_id, word_id):
    words_progress_db.increment_word_reps(chat_id, word_id)
    progress_df = words_progress_db.get_progress_df()
    num_reps = progress_df.loc[(progress_df['chat_id'] == chat_id) &
                               (progress_df['word_id'] == word_id), 'num_reps'].item()
    if num_reps > 5:
        words_progress_db.add_known_word(chat_id, word_id)


if __name__ == '__main__':
    client = 'openai'

    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action='store_true', help="Run locally")
    parser.add_argument("--webhook", type=str, help="Webhook for telegram")
    args = parser.parse_args()

    if not args.local and args.webhook is None:
        print('Webhook must be specified when not running locally.')
        exit()

    lock = threading.Lock()

    running_commands = dict()
    running_exercises_file = 'running_exercise.jb'
    if os.path.exists(running_exercises_file):
        # store running exercises
        running_exercises = joblib.load(filename=running_exercises_file)
        os.remove(running_exercises_file)
    else:
        running_exercises = dict()

    bot_token = os.getenv('BOT_TOKEN')
    if bot_token is None:
        with open(Path('api_keys/bot_token.txt'), 'r') as fp:
            bot_token = fp.read()

    openai.api_key = os.getenv('OPENAI_KEY')
    openai.organization = os.getenv('OPENAI_ORG')
    if openai.api_key is None or openai.organization:
        with open(Path('api_keys/openai_api.txt'), 'r') as fp:
            lines = fp.readlines()
            openai.api_key = lines[0].strip()
            openai.organization = lines[1].strip()

    words_db_path = os.getenv('CH_WORDS_DB_PATH')
    words_db_path = 'resources/words_db.csv' if words_db_path is None else words_db_path

    reading_db_path = os.getenv('CH_READING_DB_PATH')
    reading_db_path = 'resources/reading_lists.json' if reading_db_path is None else reading_db_path

    words_progress_db_path = os.getenv('CH_WORDS_PROGRESS_DB_PATH')
    words_progress_db_path = 'user_data/words_progress_db.csv' if words_progress_db_path is None else words_progress_db_path

    user_config_path = os.getenv('CH_USER_CONFIG_PATH')
    user_config_path = 'user_data/user_config.json' if user_config_path is None else user_config_path

    words_db = WordsDB(words_db_path)
    reading_db = ReadingDB(reading_db_path)
    words_progress_db = WordsProgressDB(words_progress_db_path)
    user_config = UserConfig(user_config_path)

    lp = LearningPlan(words_progress_db=words_progress_db, words_db=words_db,
                      reading_db=reading_db, user_config=user_config)

    # list of known exercise buttons
    exercise_buttons = ['Ignore this word', 'Hint', 'Correct answer', 'I know this word']

    port = 5001
    hookproc = None
    secret_token = None
    signal(SIGINT, lambda s, f: exithandler(s, f, hookproc))
    handle_webhook(local=args.local, url=args.webhook)

    handle_job_queue(user_config)

    serve(app, host="0.0.0.0", port=port, url_scheme='https')
