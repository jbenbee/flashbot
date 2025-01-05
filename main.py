import asyncio
import json
import os.path
from datetime import datetime, timedelta
from pathlib import Path
import signal
from zoneinfo import ZoneInfo

import jinja2
from flask import Flask
import requests

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler, CallbackQueryHandler, ContextTypes
import telegramify_markdown

from decks_db import DecksDB
from learning_plan import LearningPlan
from templates import Templates
from utils import get_audio
from words_progress_db import WordsProgressDB
from user_config import UserConfig
from words_db import WordsDB
from words_exercise import FlashcardExercise, WordsExerciseLearn, WordsExerciseTest
from running_commands import RunningCommands
from running_exercises import RunningExercises


import logging

# Disable optional logging
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)


app = Flask(__name__)

BOT_TOKEN_ENG = os.getenv('BOT_TOKEN_ENG')
BOT_TOKEN_RU = os.getenv('BOT_TOKEN_RU')


async def handle_new_exercise(bot, chat_id, exercise):
    try:
        user_data = user_config.get_user_data(chat_id)
        lang = user_data['language']
        uilang = lang_map[bot.token]

        running_exercises.add_exercise(chat_id, exercise)
        if isinstance(exercise, WordsExerciseLearn):
            lp.process_response(chat_id, exercise, quality=None)
            words_progress_db.save_progress()

        try:        
            message, _ = await exercise.get_next_user_message(user_response=None)

            buttons = None
            if isinstance(exercise, WordsExerciseLearn):
                buttons = [(exercise.uid, 'Next'), (exercise.uid, 'Discard'), (exercise.uid, 'I know this word'), (exercise.uid, 'Pronounce')]
            elif isinstance(exercise, WordsExerciseTest):
                buttons = [(exercise.uid, 'Hint'), (exercise.uid, 'Correct answer'), (exercise.uid, 'Answer audio')]
            elif isinstance(exercise, FlashcardExercise):
                buttons = [(exercise.uid, 'Discard'), (exercise.uid, 'I know this word'), (exercise.uid, 'Correct answer')]
        except Exception as e:
            message = f'{interface["Error"][uilang]}: {e}'
            buttons = None

        await tel_send_message(bot, chat_id, message, buttons=buttons)
    except Exception as e:
        if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)
        if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)
        release_all_locks()
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])


async def ping_user(bot, chat_id, lang, exercise_type, exercise_data):
    uilang = lang_map[bot.token]
    if exercise_type == 'words':
        exercise = await lp.get_next_words_exercise(chat_id, lang, mode=exercise_data)
    else:
        raise ValueError(f'Unknown exercise type {exercise_type}')

    if exercise is None:
        await tel_send_message(bot, chat_id, interface['Could not create an exercise, will try again later'][uilang])
        print(f'Could not create an exercise {exercise_type} for data {exercise_data}.')
    else:
        await handle_new_exercise(bot, chat_id, exercise)


async def tel_send_audio(bot, chat_id, audio_file_path):
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
            f"https://api.telegram.org/bot{bot.token}/sendAudio",
            data=payload,
            files=files)
        resp.json()


async def tel_send_message(bot, chat_id, text, buttons=None):

    # determine max line length to know if to display buttons on separate lines or on the same one
    lines = text.split('\n')
    max_len = max([len(line) for line in lines])
    uilang = lang_map[bot.token]
    reply_markup = None
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
        reply_markup = {
            "inline_keyboard": buttons_to_send
        }

    converted = telegramify_markdown.markdownify(text)
    await bot.send_message(chat_id, converted, reply_markup=reply_markup,  parse_mode="MarkdownV2")


async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    bot = context._application.bot
    uilang = lang_map[bot.token]
    command = update.message.text[1:]
    chat_id = update.message.chat_id

    print(f'{chat_id}, {command}')
    
    if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)
    if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)

    try:
        if command == 'add_word':
            await handle_add_word(update, context)
        elif command == 'next_new':
            await handle_next_new(update, context)
        elif command == 'next_test':
            await handle_next_test(update, context)
    except Exception as e:
        if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)
        if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)
        release_all_locks()
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])


async def handle_add_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    bot = context._application.bot
    uilang = lang_map[bot.token]
    running_commands.add_command(chat_id, 'add_word')
    await tel_send_message(bot, chat_id, interface['Type the word that you would like to add'][uilang])


async def handle_next_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    bot = context._application.bot
    uilang = lang_map[bot.token]
    lang = user_config.get_user_data(chat_id)['language']
    await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
    exercise = await lp.get_next_words_exercise(chat_id, lang, mode='test')
    if exercise is None:
        await tel_send_message(bot, chat_id, interface['Could not create an exercise, please try again later'][uilang])
        print(f'Could not create an exercise "words" for data "test".')
    else:
        await handle_new_exercise(bot, chat_id, exercise)


async def handle_next_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    bot = context._application.bot
    uilang = lang_map[bot.token]
    lang = user_config.get_user_data(chat_id)['language']
    await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
    exercise = await lp.get_next_words_exercise(chat_id, lang, mode='learn')
    if exercise is None:
        await tel_send_message(bot, chat_id, interface['Could not create an exercise, please try again later'][uilang])
        print(f'Could not create an exercise "words" for data "learn".')
    else:
        await handle_new_exercise(bot, chat_id, exercise)


async def handle_exercise_button_press(update, context, chat_id, lang, udata, exercise):
    try:
        print(f'Received a button: {udata}')
        bot = context._application.bot
        uilang = lang_map[bot.token]
        button_id = udata.split('_')[-1]
        bot = context._application.bot
        if button_id != exercise.uid:
            await tel_send_message(bot, chat_id, interface['Sorry, the exercise has been completed or is expired'][uilang])
            print(f'Button id {button_id} does not match exercise id {exercise.uid}')
        else:
            if isinstance(exercise, WordsExerciseLearn) or isinstance(exercise, WordsExerciseTest) or isinstance(exercise, FlashcardExercise):
                if f'Discard_{exercise.uid}' == udata:
                    words_progress_db.ignore_word(chat_id, exercise.word_id)
                    words_progress_db.save_progress()

                    message_template = templates.get_template(uilang, lang, 'word_ignore_message')
                    template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                    mes = template.render(word=exercise.word)
                    await tel_send_message(bot, chat_id, mes)

                elif f'Hint_{exercise.uid}' == udata:

                    running_exercise = running_exercises.pop_exercise(chat_id)
                    running_exercise.hint_clicked = True
                    running_exercises.add_exercise(chat_id, running_exercise)

                    message_template = templates.get_template(uilang, lang, 'hint_message')
                    template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                    mes = template.render(word=exercise.word)
                    await tel_send_message(bot, chat_id, mes)

                elif f'Correct answer_{exercise.uid}' == udata:

                    running_exercise = running_exercises.pop_exercise(chat_id)
                    running_exercise.correct_answer_clicked = True
                    running_exercises.add_exercise(chat_id, running_exercise)

                    await tel_send_message(bot, chat_id, exercise.correct_answer())
                elif f'I know this word_{exercise.uid}' == udata:
                    lp.set_word_easy(chat_id, exercise.word_id)
                    words_progress_db.save_progress()

                    message_template = templates.get_template(uilang, lang, 'know_word_message')
                    template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                    mes = template.render(word=exercise.word)
                    await tel_send_message(bot, chat_id, mes)

                    progress_df = words_progress_db.get_progress_df()
                    n_seen_words = progress_df[progress_df['chat_id'] == chat_id].shape[0]

                    if n_seen_words % 10 == 0:

                        message_template = templates.get_template(uilang, lang, 'congrats_learn_message')
                        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                        mes = template.render(n_seen_words=n_seen_words)
                        await tel_send_message(bot, chat_id, mes)

                elif f'Answer audio_{exercise.uid}' == udata:
                    file_path = f'{chat_id}_{exercise.uid}.mp3'
                    get_audio(exercise.correct_answer(), exercise.lang, file_path)
                    await tel_send_audio(bot, chat_id, file_path)
                    os.remove(file_path)
                elif f'Pronounce_{exercise.uid}' == udata:
                    file_path = f'{chat_id}_{exercise.uid}.mp3'
                    get_audio(exercise.word, exercise.lang, file_path)
                    await tel_send_audio(bot, chat_id, file_path)
                    os.remove(file_path)
                elif f'Next_{exercise.uid}' == udata:
                    await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
                    mode = 'learn' if isinstance(exercise, WordsExerciseLearn) else 'test'
                    exercise = await lp.get_next_words_exercise(chat_id, lang, mode)
                    if exercise is None:
                        await tel_send_message(bot, chat_id, interface['Could not create an exercise, please try again later'][uilang])
                        print(f'Could not create an exercise "words" for data "{mode}".')
                    else:
                        await handle_new_exercise(bot, chat_id, exercise)
                else:
                    raise ValueError(f'Unknown callback data {udata}')
            else:
                raise ValueError(f'Unknown exercise type: {type(exercise)}.')
    except Exception as e:
        if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)
        if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)
        release_all_locks()
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])


def execute_command_message(context, chat_id, lang, command, msg):
    bot = context._application.bot
    uilang = lang_map[bot.token]
    if command == 'add_word':
        words = msg.strip().split('\n')
        custom_deck_id = decks_db.get_custom_deck_id(str(chat_id), lang)
        for word in words:
            word_id = words_db.add_new_word(word, lang)
            decks_db.add_new_word(custom_deck_id, word_id)
        words_db.save_words_db()
        decks_db.save_decks_db()

        message_template = templates.get_template(uilang, lang, 'add_word_message')
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        user_msg = template.render(word=word)

    else:
        raise ValueError(f'Unexpected command {command}.')
    return user_msg


async def handle_inline_request(update, context):
    try:
        bot = context._application.bot
        uilang = lang_map[bot.token]
        chat_id = update.callback_query.from_user.id
        lang = user_config.get_user_data(chat_id)['language']

        data = update.callback_query.data
        button_text = data.split('_')[0]
        
        if button_text in exercise_buttons:
            # user pressed a button for an exercise
            if chat_id in running_commands.chat_ids:
                # if there were any running commands, remove them when the user interacts with an exercise
                running_commands.pop_command(chat_id)
            if chat_id in running_exercises.chat_ids:
                exercise = running_exercises.current_exercise(chat_id)
                await handle_exercise_button_press(update, context, chat_id, lang, data, exercise)
            else:
                await tel_send_message(bot, chat_id, interface['Sorry, the exercise has been completed or is expired'][uilang])
        else:
            await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])
            print(f'Unexpected inline request: {data}')

    except Exception as e:
        if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)
        if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)
        release_all_locks()
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])


async def handle_request(update, context):
    try:
        chat_id = update.message.chat_id
        bot = context._application.bot
        uilang = lang_map[bot.token]
        msg = update.message.text
        print(f'{chat_id} message: {msg}')

        lang = user_config.get_user_data(chat_id)['language']
        
        if chat_id in running_commands.chat_ids:
            # handle an input for a command
            command = running_commands.pop_command(chat_id)
            if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)

            user_msg = execute_command_message(context, chat_id, lang, command, msg)
            await tel_send_message(bot, chat_id, user_msg)
        elif chat_id in running_exercises.chat_ids:
            # user responded to an exercise
            exercise = running_exercises.current_exercise(chat_id)
            if not exercise.is_responded:

                exercise.is_responded = True
                if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)

                await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
                message, quality = await exercise.get_next_user_message(user_response=msg)
                
                buttons = None
                if isinstance(exercise, WordsExerciseLearn):
                    buttons = [(exercise.uid, 'Discard'), (exercise.uid, 'I know this word')]
                elif isinstance(exercise, WordsExerciseTest) or isinstance(exercise, FlashcardExercise):
                    lp.process_response(chat_id, exercise, quality=quality)
                    words_progress_db.save_progress()
                    buttons = [(exercise.uid, 'Discard'), (exercise.uid, 'I know this word'), (exercise.uid, 'Next')]

                await tel_send_message(bot, chat_id, message, buttons=buttons)
                words_progress_db.save_progress()
            else:
                await tel_send_message(bot, chat_id, f'{interface["The exercise has already been answered, this message will be ignored"][uilang]}: {msg}')
                print(f'The exercise has already been answered, this message will be ignored: {msg}')

        else:
            await tel_send_message(bot, chat_id, f'{interface["No exercises or commands are running, this message will be ignored"][uilang]}: {msg}')
            print(f'No running commands or exercises, ignore user message: {msg}')

    except Exception as e:
        if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)
        if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)
        release_all_locks()
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])
    return


def release_all_locks():
    for shared_obj in shared_objs:
        shared_obj.release_lock()


async def ping_users(context):
    user_data = user_config.get_all_user_data()
    bot = context._application.bot
    uilang = lang_map[bot.token]
    is_weekend = (datetime.now().weekday() == 5 or datetime.now().weekday() == 6)
    schedule_col = 'weekend' if is_weekend else 'weekday'

    users_to_ping = []
    for chat_id, v in user_data.items():
        if v['ui_language'] != uilang: continue

        user_now = datetime.now(tz=ZoneInfo(user_data[chat_id]["timezone"]))

        if 'words' in user_data[chat_id]['exercise_types']:
            ping_schedule = user_data[chat_id]['schedule']['words'][schedule_col]

            user_ping_times = [datetime.combine(user_now.date(), ptime) for ptime in ping_schedule.keys()]

            ping_schedule = [pexersize for uptime, pexersize in zip(user_ping_times, ping_schedule.values()) 
                                if abs(uptime - user_now) <= timedelta(minutes=1)]
            if len(ping_schedule) == 0:
                continue
            users_to_ping.append(dict(chat_id=chat_id, lang=user_data[chat_id]['language'], exercise=ping_schedule[0]))
    
    try:
        for user in users_to_ping:
            await ping_user(bot, user['chat_id'], user['lang'], 'words', user['exercise'])
    except Exception as e:
        if chat_id in running_commands.chat_ids: running_commands.pop_command(chat_id)
        if chat_id in running_exercises.chat_ids: running_exercises.pop_exercise(chat_id)
        release_all_locks()
        print(e)
        # await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])


def nearest_start_time(ping_interval=15 * 60):

    n_pings_per_hour = 60 * 60 // ping_interval
    ping_times = [ping_interval * (i + 1) // 60 for i in range(n_pings_per_hour)]
    if 60 not in ping_times:
        ping_times.append(60)

    now = datetime.now()
    starting_point_min = [ping_time for ping_time in ping_times if ping_time - now.minute >= 1][0]

    next_starting_point = now + timedelta(minutes=starting_point_min-now.minute)

    next_sec = (next_starting_point-datetime.now()).seconds
    if next_sec < 0: next_sec = 10
    return next_sec


async def run_apps(apps):

    stop_event = asyncio.Event()
    
    def signal_handler(signum, frame):
        print('SIGINT or CTRL-C detected. Exiting gracefully')

        # store running exercises
        running_exercises.backup()
    
        stop_event.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    for app in apps:
        await app.initialize()
        await app.start()
    
    try:
        polling_tasks = [
            asyncio.create_task(
                app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            ) for app in apps
        ]
        await stop_event.wait()

    finally:

        for app in apps:
            if app.updater.running:
                await app.updater.stop()

        for task in polling_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        for app in apps:
            await app.stop()
            await app.shutdown()


if __name__ == '__main__':

    running_commands = RunningCommands()
    running_exercises_file = 'running_exercise.jb'
    running_exercises = RunningExercises(running_exercises_file)

    user_data_root = os.getenv('CH_USER_DATA_ROOT')
    user_data_root = 'resources' if user_data_root is None else user_data_root
    user_data_root = Path(user_data_root)
    words_db_path = user_data_root / 'words_db.csv'
    decks_db_path = user_data_root / 'decks_db.csv'
    deck_word_db_path = user_data_root / 'deck_word.csv'
    words_progress_db_path = user_data_root / 'words_progress_db.csv'
    user_config_path = user_data_root / 'user_config.json'

    TIMEZONE = os.getenv('TIMEZONE')

    words_db = WordsDB(words_db_path)
    decks_db = DecksDB(decks_db_path, deck_word_db_path)
    words_progress_db = WordsProgressDB(words_progress_db_path)
    user_config = UserConfig(user_config_path)

    with open('resources/interface.json', 'r', encoding='utf-8') as fp:
        interface = json.loads(fp.read())

    templates = Templates(str(Path('resources/templates')))

    lp = LearningPlan(interface, templates, words_progress_db=words_progress_db, words_db=words_db, decks_db=decks_db, user_config=user_config)

    shared_objs = [user_config, words_db, words_progress_db, decks_db, running_exercises, running_commands]

    # list of known exercise buttons
    exercise_buttons = ['Discard', 'Hint', 'Correct answer', 'I know this word', 'Answer audio', 'Next', 'Pronounce']

    apps = []
    lang_map = {}
    for bidx, (token, lang) in enumerate(zip([BOT_TOKEN_ENG, BOT_TOKEN_RU], ['english', 'russian'])):
        application = Application.builder().token(token).build()

        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_request))
        application.add_handler(CommandHandler("add_word", handle_command))
        application.add_handler(CommandHandler("next_test", handle_command))
        application.add_handler(CommandHandler("next_new", handle_command))
        application.add_handler(CallbackQueryHandler(handle_inline_request))

        job_queue = application.job_queue
        ping_interval = 15 * 60
        first_ping = nearest_start_time(ping_interval)
        print(f"First ping scheduled in: {first_ping} sec")
        
        job_queue.run_repeating(ping_users, interval=ping_interval, first=first_ping, job_kwargs={'misfire_grace_time': None})
        apps.append(application)
        lang_map[token] = lang

    asyncio.run(run_apps(apps))
