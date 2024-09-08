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

import jinja2
import joblib
import numpy as np
import pandas as pd
from flask import Flask
from flask import request
from waitress import serve
import requests
import argparse

import openai
import urllib.parse

from decks_db import DecksDB
from learning_plan import LearningPlan
from words_progress_db import WordsProgressDB
from reading_db import ReadingDB
from reading_exercise import ReadingExercise
from user_config import UserConfig
from words_db import WordsDB
from words_exercise import WordsExerciseLearn, WordsExerciseTest
from running_commands import RunningCommands
from running_exercises import RunningExercises


app = Flask(__name__)

# make all prompts in english

def get_assistant_response(query, tokens, model_base, model_substitute, uilang, response_format=None, validation_cls=None):
    """
    model_base is tried first and if the request fails a few times, model_substitute is used instead
    """
    nattempts = 0
    messages = [
                {"role": "system", "content": interface["You are a great language teacher"][uilang]},
                {"role": "user", "content": query},
            ]
    model = model_base
    max_attempts = 3
    while nattempts < max_attempts:
        nattempts += 1
        try:
            print(f'Sending a request to chatgpt ({model})...')
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=tokens,
                response_format=response_format
            )
            break
        except openai.OpenAIError:
            time.sleep(nattempts)
        if nattempts == max_attempts - 1:
            model = model_substitute
    print('Done.')

    if nattempts == 3:
        print('The assistant raised an error.')
        raise ValueError('The model could not respond in required format')
    if response.choices[0].message.refusal:
        print('The assistant refused to respond.')
        raise ValueError('The model refused to respond')

    if validation_cls is not None:
        validated_resp = validation_cls.model_validate_json(response.choices[0].message.content)
    else:
        validated_resp = response.choices[0].message.content

    return validated_resp


def refused_answer(text):
    return any([m in text.lower() for m in ['простите', 'извините', 'извини', 'прости', 'пожалуйста', 'mi dispiace', 'i am sorry', "i'm sorry", "lo siento", 'per favore', 'por favor']])


def get_audio(query, file_path):
    response = client.audio.speech.create(
      model="tts-1",
      voice="alloy",
      input=query
    )
    response.stream_to_file(file_path)


def handle_new_exercise(chat_id, exercise):
    uilang = user_config.get_user_ui_lang(chat_id)

    tokens = user_config.get_user_data(chat_id)['max_tokens']

    running_exercises.add_exercise(chat_id, exercise)
    if isinstance(exercise, WordsExerciseLearn):
        increment_word_reps(chat_id, exercise.word_id)
        words_progress_db.save_progress()
    next_query, response_format, validation_cls, is_last_query = exercise.get_next_assistant_query(user_response=None)
    lang = user_config.get_user_data(chat_id)['language']
    model = assistant_model_cheap if (lang not in ['uzbek']) else assistant_model_good
    try:
        assistant_response = get_assistant_response(next_query, tokens, model_base=model,
                                                    model_substitute=assistant_model_good, uilang=uilang,
                                                    response_format=response_format, validation_cls=validation_cls)
        message = exercise.get_next_message_to_user(next_query, assistant_response)
        buttons = None
        if isinstance(exercise, WordsExerciseLearn):
            buttons = [(exercise.uid, 'Next'), (exercise.uid, 'Ignore this word'), (exercise.uid, 'I know this word')]
        if isinstance(exercise, WordsExerciseTest):
            buttons = [(exercise.uid, 'Hint'), (exercise.uid, 'Correct answer'), (exercise.uid, 'Answer audio')]
    except Exception as e:
        message = f'Error: {e}'
        buttons = None

    tel_send_message(chat_id, message, buttons=buttons)


def get_new_word_exercise(chat_id, lang, exercise_data):
    exercise = lp.get_next_words_exercise(chat_id, lang, mode=exercise_data)
    uilang = user_config.get_user_ui_lang(chat_id)
    if exercise is None and exercise_data == 'test':
        # there are no words to test
        tel_send_message(chat_id, interface['All words in the deck are already learned, repeating already learned words'][uilang])
        print(f'There are no exercises of type "words" for data {exercise_data}.')
        exercise = lp.get_next_words_exercise(chat_id, lang, mode='repeat_test')
    elif exercise is None and exercise_data == 'learn':
        print(f'There are no exercises of type "words" for data {exercise_data}.')
        exercise = lp.get_next_words_exercise(chat_id, lang, mode='repeat_learn')
    return exercise


def ping_user(chat_id, lang, exercise_type, exercise_data):
    uilang = user_config.get_user_ui_lang(chat_id)
    if exercise_type == 'reading':
        exercise = lp.get_next_reading_exercise(chat_id, lang, topics=exercise_data)
    elif exercise_type == 'words':
        exercise = get_new_word_exercise(chat_id, lang, exercise_data)
    else:
        raise ValueError(f'Unknown exercise type {exercise_type}')

    if exercise is None:
        tel_send_message(chat_id, interface['Could not create an exercise, will try again later'][uilang])
        print(f'Could not create an exercise {exercise_type} for data {exercise_data}.')
    else:
        handle_new_exercise(chat_id, exercise)


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


def tel_send_audio(chat_id, audio_file_path):
    uilang = user_config.get_user_ui_lang(chat_id)
    bot_token = bot_tokens[uilang]

    with open(audio_file_path, 'rb') as audio_file:
        payload = {
            'chat_id': str(chat_id),
            'title': 'audio.mp3',
            'parse_mode': 'HTML'
        }
        files = {
            'audio': audio_file.read(),
        }
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendAudio",
            data=payload,
            files=files).json()


def tel_send_message(chat_id, text, buttons=None):
    uilang = user_config.get_user_ui_lang(chat_id)
    bot_token = bot_tokens[uilang]

    url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': text
    }

    # determine max line length to know if to display buttons on separate lines or on the same one
    lines = text.split('\n')
    max_len = max([len(line) for line in lines])
    uilang = user_config.get_user_ui_lang(chat_id)
    if buttons is not None:
        buttons_list = []
        for button_id, button_text in buttons:
            buttons_list.append(
                {
                    "text": interface[button_text][uilang] if button_text in interface.keys() else button_text,
                    "callback_data": f"{button_text}_{button_id}" if button_id is not None else button_text
                }
            )

        buttons_to_send = [[button] for button in buttons_list] if max_len < 74 else [buttons_list]
        payload['reply_markup'] = {
            "inline_keyboard": buttons_to_send
        }
    bot_port = bot_ports_lang[uilang]
    r = requests.post(f'{url}', json=payload)
    return r


def handle_commands(chat_id, lang, command):
        # user pressed a command
        print(f'Received a command: {command}')
        uilang = user_config.get_user_ui_lang(chat_id)
        if command == '/next_test':
            tel_send_message(chat_id, f'{interface["Thinking"][uilang]}...')
            exercise = get_new_word_exercise(chat_id, lang, 'test')
            if exercise is None:
                tel_send_message(chat_id, interface['Could not create an exercise, please try again later'][uilang])
                print(f'Could not create an exercise "words" for data "test".')
            else:
                handle_new_exercise(chat_id, exercise)
        elif command == '/next_new':
            tel_send_message(chat_id, f'{interface["Thinking"][uilang]}...')
            exercise = get_new_word_exercise(chat_id, lang, 'learn')
            if exercise is None:
                tel_send_message(chat_id, interface['Could not create an exercise, please try again later'][uilang])
                print(f'Could not create an exercise "words" for data "learn".')
            else:
                handle_new_exercise(chat_id, exercise)
        elif command == '/next_reading':
            tel_send_message(chat_id, f'{interface["Thinking"][uilang]}...')
            exercise = lp.get_next_reading_exercise(chat_id, lang)
            if exercise is None:
                tel_send_message(chat_id, interface['Sorry, there are no reading topics for you'][uilang])
            else:
                handle_new_exercise(chat_id, exercise)
        elif command == '/add_word':
            cur_deck_id = user_config.get_user_data(chat_id)['current_deck_id']
            if decks_db.is_deck_owner(str(chat_id), cur_deck_id):
                running_commands.add_command(chat_id, command)
                tel_send_message(chat_id, interface['Type the word that you would like to add'][uilang])
            else:
                tel_send_message(chat_id, interface['You can only add words to the decks that you created'][uilang])
        elif command == '/known_words':
            tel_send_message(chat_id, f'Thinking...')
            known_words = get_known_words(chat_id, lang)
            known_words_str = "\n".join(known_words)

            message_template = templates[uilang]['known_words_info_message']
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            mes = template.render(n_known_words=len(known_words), list_known_words=known_words_str)
            tel_send_message(chat_id, mes)

        elif command == '/cur_deck_info':
            cur_deck_id = user_config.get_user_data(chat_id)['current_deck_id']
            cur_deck_name = decks_db.get_deck_name(cur_deck_id)
            deck_info = get_deck_info(chat_id, lang, cur_deck_id)

            message_template = templates[uilang]['current_deck_info_message']
            template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            mes = template.render(cur_deck_name=cur_deck_name, deck_info=deck_info)
            tel_send_message(chat_id, mes)

        elif command == '/sel_deck':
            running_commands.add_command(chat_id, command)
            decks = decks_db.get_decks_lang(str(chat_id), lang)
            if len(decks) > 0:
                buttons = [(deck['id'], deck['name']) for deck in decks]
                tel_send_message(chat_id, f'{interface["Create a new deck by typing its name or select an existing deck"][uilang]}:', buttons=buttons)
            else:
                tel_send_message(chat_id, interface['Create a new deck by typing its name'][uilang])
        else:
            raise ValueError(f'Unknown command {command}')


def handle_exercise_button_press(chat_id, lang, udata, exercise):
    print(f'Received a button: {udata}')
    uilang = user_config.get_user_ui_lang(chat_id)
    button_id = udata.split('_')[-1]
    if button_id != exercise.uid:
        tel_send_message(chat_id, interface['Sorry, the exercise has been completed or is expired'][uilang])
        print(f'Button id {button_id} does not match exercise id {exercise.uid}')
    else:
        if isinstance(exercise, WordsExerciseLearn) or isinstance(exercise, WordsExerciseTest):
            if f'Ignore this word_{exercise.uid}' == udata:
                words_progress_db.ignore_word(chat_id, exercise.word_id)
                words_progress_db.save_progress()

                message_template = templates[uilang]['word_ignore_message']
                template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                mes = template.render(word=exercise.word)
                tel_send_message(chat_id, mes)

            elif f'Hint_{exercise.uid}' == udata:

                message_template = templates[uilang]['hint_message']
                template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                mes = template.render(word=exercise.word)
                tel_send_message(chat_id, mes)

            elif f'Correct answer_{exercise.uid}' == udata:
                tel_send_message(chat_id, exercise.correct_answer())
            elif f'I know this word_{exercise.uid}' == udata:
                words_progress_db.add_known_word(chat_id, exercise.word_id)
                words_progress_db.save_progress()

                message_template = templates[uilang]['know_word_message']
                template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                mes = template.render(word=exercise.word)
                tel_send_message(chat_id, mes)

                known_words = get_known_words(chat_id, lang)
                n_known_words = len(known_words)
                if n_known_words % 5 == 0:

                    message_template = templates[uilang]['congrats_learn_message']
                    template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                    mes = template.render(n_known_words=n_known_words)
                    tel_send_message(chat_id, mes)

            elif f'Answer audio_{exercise.uid}' == udata:
                file_path = f'{chat_id}_{exercise.uid}.mp3'
                get_audio(exercise.correct_answer(), file_path)
                tel_send_audio(chat_id, file_path)
                os.remove(file_path)
            elif f'Next_{exercise.uid}' == udata:
                tel_send_message(chat_id, f'{interface["Thinking"][uilang]}...')
                exercise = get_new_word_exercise(chat_id, lang, 'learn')
                if exercise is None:
                    tel_send_message(chat_id, interface['Could not create an exercise, please try again later'][uilang])
                    print(f'Could not create an exercise "words" for data "learn".')
                else:
                    handle_new_exercise(chat_id, exercise)
            else:
                raise ValueError(f'Unknown callback data {udata}')
        else:
            raise ValueError(f'Unknown exercise type: {type(exercise)}.')


def execute_command_button(chat_id, lang, command, button_data):
    uilang = user_config.get_user_ui_lang(chat_id)
    if command == '/sel_deck':
        # choose an existing deck
        cur_deck_name, cur_deck_id = button_data.split('_')
        cur_deck_id = int(cur_deck_id)
        user_config.set_deck(str(chat_id), cur_deck_id)
        deck_info = get_deck_info(chat_id, lang, cur_deck_id)

        message_template = templates[uilang]['sel_deck_message']
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        user_msg = template.render(cur_deck_name=cur_deck_name, deck_info=deck_info)

    else:
        raise ValueError(f'Unexpected command {command}.')
    return user_msg


def execute_command_message(chat_id, lang, command, msg):
    uilang = user_config.get_user_ui_lang(chat_id)
    if command == '/add_word':
        # word = msg.strip()
        words = msg.strip().split('\n')
        cur_deck_id = user_config.get_user_data(chat_id)['current_deck_id']
        for word in words:
            word_id = words_db.add_new_word(word, lang)
            decks_db.add_new_word(cur_deck_id, word_id)
        words_db.save_words_db()
        decks_db.save_decks_db()
        cur_deck = decks_db.get_deck_name(cur_deck_id)

        message_template = templates[uilang]['add_word_message']
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        user_msg = template.render(word=word, cur_deck=cur_deck)

    elif command == '/sel_deck':
        # create a new deck
        deck_id = decks_db.create_deck(str(chat_id), msg, lang)
        user_config.set_deck(str(chat_id), deck_id)
        decks_db.save_decks_db()

        message_template = templates[uilang]['create_deck_message']
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        user_msg = template.render(msg=msg)

    else:
        raise ValueError(f'Unexpected command {command}.')
    return user_msg


def handle_user_message(chat_id, lang, tokens, msg):
    uilang = user_config.get_user_ui_lang(chat_id)
    # if chat_id in running_commands.keys():
    if chat_id in running_commands.chat_ids:
        # handle an input for a command
        command = running_commands.pop_command(chat_id)
        user_msg = execute_command_message(chat_id, lang, command, msg)
        tel_send_message(chat_id, user_msg)
    # elif chat_id in running_exercises.keys():
    #     exercise = running_exercises.pop(chat_id)
    elif chat_id in running_exercises.chat_ids:
        #exercise = running_exercises.pop_exercise(chat_id)
        exercise = running_exercises.current_exercise(chat_id)

        tel_send_message(chat_id, 'Thinking...')
        next_query, response_format, validation_cls, is_last_query = exercise.get_next_assistant_query(user_response=msg)
        assistant_response = get_assistant_response(next_query, tokens, model_base=assistant_model_cheap,
                                                    model_substitute=assistant_model_good,
                                                    uilang=uilang, response_format=response_format, validation_cls=validation_cls)
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
        tel_send_message(chat_id, f'{interface["No exercises or commands are running, this message will be ignored"][uilang]}: {msg}')
        print(f'No running commands or exercises, ignore user message: {msg}')


def release_all_locks():
    for shared_obj in shared_objs:
        shared_obj.release_lock()


def handle_request(msg):
    chat_id, type, data = parse_message(msg)
    print(f'{chat_id} message: {data}')

    lang = user_config.get_user_data(chat_id)['language']
    tokens = user_config.get_user_data(chat_id)['max_tokens']
    uilang = user_config.get_user_ui_lang(chat_id)
    # lock.acquire()
    try:
        if type == 'command':
            # if chat_id in running_exercises.keys():
            if chat_id in running_exercises.chat_ids:
                # if there were any running exercises, remove them after a command has been pressed
                # running_exercises.pop(chat_id)
                running_exercises.pop_exercise(chat_id)
            handle_commands(chat_id, lang, data)
        elif type == 'button':
            button_text = data.split('_')[0]
            if button_text in exercise_buttons:
                # user pressed a button for an exercise
                # if chat_id in running_commands.keys():
                if chat_id in running_commands.chat_ids:
                    # if there were any running commands, remove them when the user interacts with an exercise
                    running_commands.pop_command(chat_id)
                if chat_id in running_exercises.chat_ids:
                    exercise = running_exercises.current_exercise(chat_id)
                    handle_exercise_button_press(chat_id, lang, data, exercise)
                else:
                    tel_send_message(chat_id, interface['Sorry, the exercise has been completed or is expired'][uilang])
            else:
                # user pressed a button required to complete a command
                # if chat_id in running_commands.keys():
                #     command = running_commands.pop(chat_id)
                if chat_id in running_commands.chat_ids:
                    command = running_commands.pop_command(chat_id)
                    user_msg = execute_command_button(chat_id, lang, command, data)
                    tel_send_message(chat_id, user_msg)
                else:
                    tel_send_message(chat_id, interface['The command has already been processed'][uilang])
                    # raise Exception(f'The user pressed a button for a command, '
                    #                 f'but there are no running commands for user {chat_id}.')
        elif type == 'message':
            handle_user_message(chat_id, lang, tokens, data)
        else:
            raise ValueError(f'Unknown message type {type}, {data}')
    except Exception as e:
        # if chat_id in running_commands.keys(): running_commands.pop(chat_id)
        # if chat_id in running_exercises.keys(): running_exercises.pop(chat_id)
        if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)
        if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)
        release_all_locks()
        print(e)
        tel_send_message(chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])
        # raise e  # TODO
    # lock.release()


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

        x = threading.Thread(target=lambda: handle_request(msg), daemon=True)
        x.start()
        return 'ok'
    else:
        return "<h1>Welcome!</h1>"


def exithandler(signal_received, frame, proc):
    # Handle any cleanup here
    print('SIGINT or CTRL-C detected. Exiting gracefully')

    # store running exercises
    running_exercises.backup()

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

        tomorrow_ping_time = datetime.combine(datetime.today() + timedelta(days=1), earliest_time) - timedelta(
            minutes=3)
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
        secret_token = str(uuid.uuid1())
        for bot_token in bot_tokens.values():
            if hookproc[bot_token] is not None:
                page = urllib.request.urlopen(f'https://api.telegram.org/bot{bot_token}/setWebhook?remove')
                print(f'Remove webhook status: {page.getcode()}')
                hookproc[bot_token].kill()
            bport = port if args.local else bot_ports_token[bot_token]
            hookproc[bot_token] = set_webhook(bport, url, bot_token)
        time.sleep(sleep_time)  # refresh hook url


def set_webhook(port, url, bot_token):
    if url is not None:
        response = requests.post(
            url=f'https://api.telegram.org/bot{bot_token}/setWebhook',
            data={'url': f'{url}:{port}', 'secret_token': secret_token}
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


def get_known_words(chat_id, lang, word_ids=None):
    words_df = words_db.get_words_df()
    progress_df = words_progress_db.get_progress_df()
    progress_words_df = pd.merge(progress_df, words_df, how='left', left_on='word_id',
                                 right_on='id', sort=False, validate='1:1')

    words_mask = progress_words_df['id'].isin(word_ids) if word_ids is not None else np.ones(progress_words_df.shape[0])

    pw_df = progress_words_df.loc[(progress_words_df['chat_id'] == chat_id) &
                                  (progress_words_df['lang'] == lang) &
                                  words_mask &
                                  progress_words_df['is_known']]
    words = pw_df['word'].to_list()
    return words


def get_deck_info(chat_id, lang, deck_id):
    deck_words = decks_db.get_deck_words(deck_id)
    n_learned_words = len(get_known_words(chat_id, lang, word_ids=deck_words))
    group_info = f'Total number of words in the deck is {len(deck_words)}.\n' \
                 f'Number of known words in the deck is {n_learned_words}.'
    return group_info


def increment_word_reps(chat_id, word_id):
    words_progress_db.increment_word_reps(chat_id, word_id)
    progress_df = words_progress_db.get_progress_df()
    num_reps = progress_df.loc[(progress_df['chat_id'] == chat_id) &
                               (progress_df['word_id'] == word_id), 'num_reps'].item()
    if num_reps > 5:
        words_progress_db.add_known_word(chat_id, word_id)


def load_templates(path: str):
    templates = dict()

    for lang in os.listdir(path):
        lang_path = os.path.join(path, lang)

        if os.path.isdir(lang_path):
            templates[lang] = {}

            for template_name in os.listdir(lang_path):
                template_path = os.path.join(lang_path, template_name)

                if os.path.isfile(template_path):
                    with open(template_path, 'r', encoding='utf-8') as file:
                        tname = os.path.splitext(template_name)[0]
                        templates[lang][tname] = file.read()

    return templates


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action='store_true', help="Run locally")
    parser.add_argument("--webhook", type=str, help="Webhook for telegram")
    parser.add_argument("--model_cheap", type=str, default="gpt-4o-mini")
    parser.add_argument("--model_good", type=str, default="gpt-4")

    args = parser.parse_args()

    if not args.local and args.webhook is None:
        print('Webhook must be specified when not running locally.')
        exit()

    running_commands = RunningCommands()
    running_exercises_file = 'running_exercise.jb'
    running_exercises = RunningExercises(running_exercises_file)

    engbot_token = os.getenv('BOT_TOKEN')
    if engbot_token is None:
        with open(Path('api_keys/bot_token.txt'), 'r') as fp:
            engbot_token = fp.read()
    rubot_token = os.getenv('RUBOT_TOKEN')
    if rubot_token is None:
        with open(Path('api_keys/rubot_token.txt'), 'r') as fp:
            rubot_token = fp.read()

    # TODO: A user can only be assigned to one of the bots, not to multiple
    bot_tokens = {
        'english': engbot_token,
        'russian': rubot_token
    }
    bot_ports_lang = {
        'english': '443',
        'russian': '8443'
    }
    bot_ports_token = {
        engbot_token: '443',
        rubot_token: '8443'
    }

    openai_key = os.getenv('OPENAI_KEY')
    if openai_key is None:
        with open(Path('api_keys/openai_api.txt'), 'r') as fp:
                lines = fp.readlines()
                openai_key = lines[0].strip()
    client = openai.OpenAI(api_key=openai_key, timeout=20.0, max_retries=0)

    user_data_root = os.getenv('CH_USER_DATA_ROOT')
    user_data_root = 'resources' if user_data_root is None else user_data_root
    user_data_root = Path(user_data_root)
    words_db_path = user_data_root / 'words_db.csv'
    decks_db_path = user_data_root / 'decks_db.csv'
    deck_word_db_path = user_data_root / 'deck_word.csv'
    reading_db_path = user_data_root / 'reading_lists.json'
    words_progress_db_path = user_data_root / 'words_progress_db.csv'
    user_config_path = user_data_root / 'user_config.json'

    words_db = WordsDB(words_db_path)
    decks_db = DecksDB(decks_db_path, deck_word_db_path)
    reading_db = ReadingDB(reading_db_path)
    words_progress_db = WordsProgressDB(words_progress_db_path)
    user_config = UserConfig(user_config_path)

    with open(user_data_root / 'interface.json', 'r', encoding='utf-8') as fp:
        interface = json.loads(fp.read())

    templates = load_templates(str(user_data_root / 'templates'))

    lp = LearningPlan(interface, templates, words_progress_db=words_progress_db, words_db=words_db, decks_db=decks_db,
                      reading_db=reading_db, user_config=user_config)

    shared_objs = [user_config, words_db, words_progress_db, decks_db, running_exercises, running_commands]

    # list of known exercise buttons
    exercise_buttons = ['Ignore this word', 'Hint', 'Correct answer', 'I know this word', 'Answer audio', 'Next']

    port = 5001
    assistant_model_cheap = args.model_cheap
    assistant_model_good = args.model_good
    hookproc = {token: None for token in bot_tokens.values()}
    secret_token = None
    for token in bot_tokens.values():
        signal(SIGINT, lambda s, f: exithandler(s, f, hookproc[token]))

    handle_webhook(local=args.local, url=args.webhook)

    handle_job_queue(user_config)

    host = "0.0.0.0"
    serve(app, host="0.0.0.0", port=port, url_scheme='https')
