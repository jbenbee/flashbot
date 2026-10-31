import asyncio
import json
import os.path
from datetime import datetime, timedelta
from pathlib import Path
import random
import signal
import threading
from zoneinfo import ZoneInfo

import jinja2
from flask import Flask

from telegram import InputFile, Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler, CallbackQueryHandler, ContextTypes
import telegramify_markdown

from decks_db import DecksDB
from exercise import Exercise
from learning_plan import LearningPlan
from templates import Templates
from utils import get_audio, generate_song_of_the_day, get_assistant_response, get_weather_forecast
from words_progress_db import WordsProgressDB
from user_config import UserConfig
from words_db import WordsDB
from words_exercise import FlashcardExercise, WordsExerciseLearn, WordsExerciseTest
from running_activities import RunningActivities
import dotenv
from tempfile import TemporaryDirectory

import logging

dotenv.load_dotenv()

# Disable optional logging
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)


app = Flask(__name__)

BOT_TOKEN_ENG = os.getenv('BOT_TOKEN_ENG')
BOT_TOKEN_RU = os.getenv('BOT_TOKEN_RU')

BOT_TOKENS = [BOT_TOKEN_ENG]
BOT_LANGS = ['english']
if BOT_TOKEN_RU is not None:
    BOT_TOKENS.append(BOT_TOKEN_RU)
    BOT_LANGS.append('russian')

CONCURRENT_UPDATES_LIMIT = int(os.getenv('PTB_CONCURRENT_UPDATES', '8'))

busy_users: set[int] = set()
busy_users_lock = threading.Lock()

# Global cap on simultaneous song generations (protects API rate limits and
# prevents too many large file downloads at once). Reject-rather-than-queue:
# requests over the cap are turned away immediately instead of waiting.
SONG_GEN_CONCURRENT_LIMIT = int(os.getenv('SONG_GEN_CONCURRENT_LIMIT', '2'))

song_gen_in_progress = 0
song_gen_lock = threading.Lock()


def try_acquire_user_slot(chat_id):
    with busy_users_lock:
        if chat_id in busy_users:
            return False
        busy_users.add(chat_id)
        return True


def release_user_slot(chat_id):
    with busy_users_lock:
        busy_users.discard(chat_id)


def try_acquire_song_slot():
    """Reserve a global song-generation slot without waiting.

    Returns False immediately if SONG_GEN_CONCURRENT_LIMIT generations are
    already running; the caller is expected to reject the request.
    """
    global song_gen_in_progress
    with song_gen_lock:
        if song_gen_in_progress >= SONG_GEN_CONCURRENT_LIMIT:
            return False
        song_gen_in_progress += 1
        return True


def release_song_slot():
    global song_gen_in_progress
    with song_gen_lock:
        if song_gen_in_progress > 0:
            song_gen_in_progress -= 1


async def notify_user_is_busy(update, context, chat_id):
    bot = context._application.bot
    uilang = lang_map[bot.token]
    message = interface['Another request is already being processed'][uilang]

    if update is not None and update.callback_query is not None:
        try:
            await update.callback_query.answer(text=message, show_alert=False)
            return
        except Exception as e:
            print(f'Could not send busy callback answer to {chat_id}: {e}')

    await tel_send_message(bot, chat_id, message)


async def handle_new_exercise(bot, chat_id, exercise):
    try:
        user_data = await asyncio.to_thread(user_config.get_user_data, chat_id)
        lang = user_data['language']
        uilang = lang_map[bot.token]

        running_activities.add_activity(chat_id, exercise)
        if isinstance(exercise, WordsExerciseLearn):
            await asyncio.to_thread(lp.process_response, chat_id, exercise, quality=None)
            await asyncio.to_thread(words_progress_db.save_progress)

        try:
            if not isinstance(exercise, WordsExerciseLearn) and "show_words_due" in user_data.keys() and user_data['show_words_due']:
                words_due = await asyncio.to_thread(lp.get_due_today, chat_id, lang)

                message_template = templates.get_template(uilang, lang, 'words_due')
                template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                due_message = template.render(n_words=len(words_due)).strip()
                if len(words_due) > 0:
                    due_message = f'{due_message}\n\n---------------\n\n'
            else:
                due_message = ''

            message, _ = await exercise.get_next_user_message(user_response=None)

            message = f'{due_message}{message}'

            buttons = None
            if isinstance(exercise, WordsExerciseLearn):
                # buttons = ['Next', 'Discard', 'I know this word', 'Pronounce']
                buttons = ['Next', 'Discard', 'Pronounce']
            elif isinstance(exercise, WordsExerciseTest):
                buttons = ['Easier', 'Harder', 'Hint', 'Correct answer', 'Answer audio']
            elif isinstance(exercise, FlashcardExercise):
                # buttons = ['Discard', 'I know this word', 'Correct answer']
                buttons = ['Discard', 'Correct answer']
        except Exception as e:
            message = f'{interface["Error"][uilang]}: {e}'
            buttons = None

        await tel_send_message(bot, chat_id, message, buttons=buttons)
    except Exception as e:
        if chat_id in running_activities.chat_ids: running_activities.pop_all(chat_id)
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])


# async def send_song_of_the_day(bot, chat_id, prompt):
#     with TemporaryDirectory() as fp:
#         song_data = await generate_song_of_the_day(prompt=prompt, dirpath=Path(fp))

#         lyrics = '\n'.join([f'> {line}' for line in song_data['lyrics'].split('\n')])

#         await tel_send_image(bot, chat_id, song_data['img_path'], song_data['title'])
#         await tel_send_message(bot, chat_id, lyrics)
#         await tel_send_audio(bot, chat_id, song_data['audio_data'][0]['audio_file_path'], title=song_data['title'] + ' (1)')
#         await tel_send_audio(bot, chat_id, song_data['audio_data'][1]['audio_file_path'], title=song_data['title'] + ' (2)')


async def ping_user(bot, chat_id, lang, exercise_type, exercise_data):
    uilang = lang_map[bot.token]

    if not try_acquire_user_slot(chat_id):
        print(f'Skipping scheduled ping for busy user {chat_id}.')
        return

    try:
        if exercise_type == 'words':
            exercise = await lp.get_next_words_exercise(chat_id, lang, mode=exercise_data)
        else:
            raise ValueError(f'Unknown exercise type {exercise_type}')

        if exercise is None:
            await tel_send_message(bot, chat_id, interface['Could not create an exercise, will try again later'][uilang])
            print(f'Could not create an exercise {exercise_type} for data {exercise_data}.')
        else:
            await handle_new_exercise(bot, chat_id, exercise)
    finally:
        release_user_slot(chat_id)



async def tel_send_audio(bot, chat_id, audio_file_path, title='audio.mp3'):
    path = Path(audio_file_path)
    audio_data = await asyncio.to_thread(path.read_bytes)
    await bot.send_audio(chat_id=chat_id, audio=InputFile(audio_data, filename=path.name), title=title, parse_mode='HTML')

async def tel_send_image(bot, chat_id, image_file_path, caption):
    path = Path(image_file_path)
    image_data = await asyncio.to_thread(path.read_bytes)
    await bot.send_photo(chat_id=chat_id, photo=InputFile(image_data, filename=path.name), caption=caption, parse_mode='HTML')

async def tel_send_message(bot, chat_id, text, buttons=None, parse_mode="MarkdownV2"):

    # determine max line length to know if to display buttons on separate lines or on the same one
    lines = text.split('\n')
    max_len = max([len(line) for line in lines])
    uilang = lang_map[bot.token]
    reply_markup = None
    if buttons is not None:
        buttons_list = []
        for button_text in buttons:
            buttons_list.append(
                {
                    "text": interface[button_text][uilang] if button_text in interface.keys() else button_text,
                    "callback_data": button_text
                }
            )

        buttons_to_send = [[button] for button in buttons_list] if max_len < 74 else [buttons_list]
        reply_markup = {
            "inline_keyboard": buttons_to_send
        }

    converted = telegramify_markdown.markdownify(text)
    await bot.send_message(chat_id, converted, reply_markup=reply_markup,  parse_mode=parse_mode)


async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    bot = context._application.bot
    uilang = lang_map[bot.token]
    command = update.message.text[1:]
    chat_id = update.message.chat_id

    if not try_acquire_user_slot(chat_id):
        await notify_user_is_busy(update, context, chat_id)
        return

    try:
        if command == 'add_word':
            await handle_add_word(update, context)
        elif command == 'next_new':
            await handle_next_new(update, context)
        elif command == 'next_test':
            await handle_next_test(update, context)
        elif command == 'song_of_the_day':
            await handle_song_of_the_day(update, context)
    except Exception as e:
        if chat_id in running_activities.chat_ids: running_activities.pop_all(chat_id)
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])
    finally:
        release_user_slot(chat_id)


async def handle_add_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    bot = context._application.bot
    uilang = lang_map[bot.token]
    running_activities.add_activity(chat_id, 'add_word')
    await tel_send_message(bot, chat_id, interface['Type the word that you would like to add'][uilang])


async def handle_next_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    bot = context._application.bot
    uilang = lang_map[bot.token]
    user_data = await asyncio.to_thread(user_config.get_user_data, chat_id)
    lang = user_data['language']

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
    user_data = await asyncio.to_thread(user_config.get_user_data, chat_id)
    lang = user_data['language']

    await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
    exercise = await lp.get_next_words_exercise(chat_id, lang, mode='learn')
    if exercise is None:
        await tel_send_message(bot, chat_id, interface['Could not create an exercise, please try again later'][uilang])
        print(f'Could not create an exercise "words" for data "learn".')
    else:
        await handle_new_exercise(bot, chat_id, exercise)


async def handle_song_of_the_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    bot = context._application.bot
    uilang = lang_map[bot.token]
    user_data = await asyncio.to_thread(user_config.get_user_data, chat_id)
    lang = user_data['language']

    # the song of the day can only be generated once per day (based on the user's local date)
    user_tz = ZoneInfo(user_data["timezone"])
    today = datetime.now(tz=user_tz).date()
    if user_data.get('last_song_of_the_day_date') == today.isoformat():
        await tel_send_message(bot, chat_id, interface['The next song of the day will be available tomorrow'][uilang])
        return

    # reject (not queue) if too many song generations are already running globally
    if not try_acquire_song_slot():
        print(f'Rejecting song_of_the_day for {chat_id}: song generation capacity reached ({SONG_GEN_CONCURRENT_LIMIT}).')
        await tel_send_message(bot, chat_id, interface['Too many song generations are in progress, please try again later'][uilang])
        return

    try:
        await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')

        # with open('resources/song_genres.json', 'r') as fp:
        #     genres = json.loads(fp.read())
        genres = user_data.get('genres', [])

        genre_prompt = ''
        genres_sub = random.sample(genres, min(5, len(genres))) if genres else []

        # sample 5 words that the user is currently learning
        learning_words = await asyncio.to_thread(lp.get_due_today, chat_id, lang) or []
        n_sample = min(5, len(learning_words))
        sampled_words = random.sample(learning_words, n_sample) if n_sample > 0 else []

        # get the current time and weather at the user's location to tune the song to them
        local_time = datetime.now(tz=user_tz).strftime('%A, %d %B %Y, %H:%M')
        weather = await get_weather_forecast(user_tz)

        # ask the LLM for a song idea containing all the sampled words
        message_template = templates.get_template('english', lang, 'song_of_the_day_query')
        template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
        query = template.render(lang=interface[lang][uilang], words_str=', '.join(sampled_words), genres=', '.join(genres_sub), weather=weather, local_time=local_time)

        model_base = os.getenv('MODEL_BASE')
        model_substitute = os.getenv('MODEL_SUBSTITUTE')
        llm_prompt = await get_assistant_response(interface, query, uilang=uilang,
                                                  model_base=model_base, model_substitute=model_substitute)

        prompt = f"{llm_prompt}\n{genre_prompt}"

        with TemporaryDirectory() as fp:
            song_data = await generate_song_of_the_day(prompt=prompt, dirpath=Path(fp))

            lyrics = '\n'.join([f'> {line}' for line in song_data['lyrics'].split('\n')])

            await tel_send_image(bot, chat_id, song_data['img_path'], song_data['title'])
            await tel_send_message(bot, chat_id, lyrics)
            await tel_send_audio(bot, chat_id, song_data['audio_data'][0]['audio_file_path'], title=song_data['title'] + ' (1)')
            await tel_send_audio(bot, chat_id, song_data['audio_data'][1]['audio_file_path'], title=song_data['title'] + ' (2)')


        await asyncio.to_thread(user_config.set_user_field, chat_id, 'last_song_of_the_day_date', today.isoformat())
    finally:
        release_song_slot()


async def handle_exercise_button_press(update, context, chat_id, lang, udata, exercise):
    try:
        print(f'Received a button: {udata}')
        bot = context._application.bot
        uilang = lang_map[bot.token]
        bot = context._application.bot

        if isinstance(exercise, WordsExerciseLearn) or isinstance(exercise, WordsExerciseTest) or isinstance(exercise, FlashcardExercise):
            if f'Discard' == udata:
                await asyncio.to_thread(words_progress_db.ignore_word, chat_id, exercise.word_id)
                await asyncio.to_thread(words_progress_db.save_progress)

                message_template = templates.get_template(uilang, lang, 'word_ignore_message')
                template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                mes = template.render(word=exercise.word)
                await tel_send_message(bot, chat_id, mes)

            elif f'Hint' == udata:

                running_exercise = running_activities.pop_activity(chat_id)
                running_exercise.hint_clicked = True
                running_activities.add_activity(chat_id, running_exercise)

                if not exercise.is_responded:
                    await asyncio.to_thread(lp.process_hint, chat_id, running_exercise)
                    await asyncio.to_thread(words_progress_db.save_progress)

                message_template = templates.get_template(uilang, lang, 'hint_message')
                template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
                mes = template.render(word=exercise.word)
                await tel_send_message(bot, chat_id, mes)

            elif f'Correct answer' == udata:

                running_exercise = running_activities.pop_activity(chat_id)
                running_exercise.correct_answer_clicked = True
                running_activities.add_activity(chat_id, running_exercise)
                if not exercise.is_responded:
                    await asyncio.to_thread(lp.process_correct_answer, chat_id, running_exercise)
                    await asyncio.to_thread(words_progress_db.save_progress)

                await tel_send_message(bot, chat_id, exercise.correct_answer())
            # elif f'I know this word' == udata:
            #     lp.set_word_easy(chat_id, exercise.word_id)
            #     words_progress_db.save_progress()

            #     message_template = templates.get_template(uilang, lang, 'know_word_message')
            #     template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            #     mes = template.render(word=exercise.word)
            #     await tel_send_message(bot, chat_id, mes)

            #     progress_df = words_progress_db.get_progress_df()
            #     n_seen_words = progress_df[progress_df['chat_id'] == chat_id].shape[0]

            #     if n_seen_words % 10 == 0:

            #         message_template = templates.get_template(uilang, lang, 'congrats_learn_message')
            #         template = jinja2.Template(message_template, undefined=jinja2.StrictUndefined)
            #         mes = template.render(n_seen_words=n_seen_words)
            #         await tel_send_message(bot, chat_id, mes)

            elif f'Answer audio' == udata:
                file_path = f'{chat_id}_{exercise.uid}.mp3'
                try:
                    await get_audio(exercise.correct_answer(), exercise.lang, file_path)
                    await tel_send_audio(bot, chat_id, file_path)
                finally:
                    await asyncio.to_thread(Path(file_path).unlink, missing_ok=True)
            elif f'Pronounce' == udata:
                file_path = f'{chat_id}_{exercise.uid}.mp3'
                try:
                    await get_audio(exercise.word, exercise.lang, file_path)
                    await tel_send_audio(bot, chat_id, file_path)
                finally:
                    await asyncio.to_thread(Path(file_path).unlink, missing_ok=True)
            elif f'Next' == udata:
                await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
                mode = 'learn' if isinstance(exercise, WordsExerciseLearn) else 'test'
                exercise = await lp.get_next_words_exercise(chat_id, lang, mode)
                if exercise is None:
                    await tel_send_message(bot, chat_id, interface['Could not create an exercise, please try again later'][uilang])
                    print(f'Could not create an exercise "words" for data "{mode}".')
                else:
                    await handle_new_exercise(bot, chat_id, exercise)
            elif f'Easier' == udata:
                await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
                message = exercise.change_difficulty(easier=True)
                buttons = []
                if exercise.difficulty > 1:
                    buttons.append('Easier')
                if exercise.difficulty < 5:
                    buttons.append('Harder')
                buttons.extend(['Hint', 'Correct answer', 'Answer audio'])
                await tel_send_message(bot, chat_id, message, buttons=buttons)

            elif f'Harder' == udata:
                await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
                message = exercise.change_difficulty(easier=False)
                buttons = []
                if exercise.difficulty > 1:
                    buttons.append('Easier')
                if exercise.difficulty < 5:
                    buttons.append('Harder')
                buttons.extend(['Hint', 'Correct answer', 'Answer audio'])
                await tel_send_message(bot, chat_id, message, buttons=buttons)
            else:
                raise ValueError(f'Unknown callback data {udata}')
        else:
            raise ValueError(f'Unknown exercise type: {type(exercise)}.')
    except Exception as e:
        if chat_id in running_activities.chat_ids: running_activities.pop_all(chat_id)
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
    bot = context._application.bot
    uilang = lang_map[bot.token]
    chat_id = update.callback_query.from_user.id

    if not try_acquire_user_slot(chat_id):
        await notify_user_is_busy(update, context, chat_id)
        return

    try:
        user_data = await asyncio.to_thread(user_config.get_user_data, chat_id)
        lang = user_data['language']

        data = update.callback_query.data
        button_text = data.split('_')[0]
        
        if button_text in exercise_buttons:

            activity = running_activities.current_activity(chat_id)
            if not isinstance(activity, Exercise) and activity is not None:
                # a command was pressed, but was not completed
                running_activities.pop_activity(chat_id)
            activity = running_activities.current_activity(chat_id)
            if activity is not None:
                await handle_exercise_button_press(update, context, chat_id, lang, data, activity)
            else:
                await tel_send_message(bot, chat_id, interface['Sorry, the exercise has been completed or is expired'][uilang])
        else:
            await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])
            print(f'Unexpected inline request: {data}')

    except Exception as e:
        if chat_id in running_activities.chat_ids: running_activities.pop_all(chat_id)
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])
    finally:
        release_user_slot(chat_id)


async def handle_request(update, context):
    chat_id = update.message.chat_id
    bot = context._application.bot
    uilang = lang_map[bot.token]

    if not try_acquire_user_slot(chat_id):
        await notify_user_is_busy(update, context, chat_id)
        return

    try:
        msg = update.message.text
        print(f'{chat_id} message: {msg}')

        user_data = await asyncio.to_thread(user_config.get_user_data, chat_id)
        lang = user_data['language']

        current_activity = running_activities.current_activity(chat_id)

        if isinstance(current_activity, Exercise):
            # user responded to an exercise
            exercise = current_activity
            if not exercise.is_responded:
                
                exercise.is_responded = True

                await tel_send_message(bot, chat_id, f'{interface["Thinking"][uilang]}...')
                message, quality = await exercise.get_next_user_message(user_response=msg)
                
                buttons = None
                if isinstance(exercise, WordsExerciseLearn):
                    # buttons = ['Discard', 'I know this word']
                    buttons = ['Discard']
                elif isinstance(exercise, WordsExerciseTest) or isinstance(exercise, FlashcardExercise):
                    await asyncio.to_thread(lp.process_response, chat_id, exercise, quality=quality)
                    await asyncio.to_thread(words_progress_db.save_progress)
                    # buttons = ['Discard', 'I know this word', 'Next']
                    buttons = ['Discard', 'Next']

                await tel_send_message(bot, chat_id, message, buttons=buttons)
                await asyncio.to_thread(words_progress_db.save_progress)
            else:
                await tel_send_message(bot, chat_id, f'{interface["The exercise has already been answered, this message will be ignored"][uilang]}: {msg}')
                print(f'The exercise has already been answered, this message will be ignored: {msg}')
            
        elif current_activity is not None:
            # handle an input for a command
            command = current_activity
            user_msg = await asyncio.to_thread(execute_command_message, context, chat_id, lang, command, msg)
            await tel_send_message(bot, chat_id, user_msg)
            running_activities.pop_activity(chat_id)
        else:
            await tel_send_message(bot, chat_id, f'{interface["No exercises or commands are running, this message will be ignored"][uilang]}: {msg}')
            print(f'No running commands or exercises, ignore user message: {msg}')

    except Exception as e:
        if chat_id in running_activities.chat_ids: running_activities.pop_all(chat_id)
        print(e)
        await tel_send_message(bot, chat_id, interface['Something went terribly wrong, please try again or notify the admin'][uilang])
    finally:
        release_user_slot(chat_id)
    return


async def ping_users(context):
    user_data = await asyncio.to_thread(user_config.get_all_user_data)
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

    ping_limit = asyncio.Semaphore(CONCURRENT_UPDATES_LIMIT)

    async def send_scheduled_ping(user):
        async with ping_limit:
            try:
                await ping_user(bot, user['chat_id'], user['lang'], 'words', user['exercise'])
            except Exception as e:
                # clean up only the failed user's activities; other users are unaffected
                if user['chat_id'] in running_activities.chat_ids: running_activities.pop_all(user['chat_id'])
                print(e)

    await asyncio.gather(*(send_scheduled_ping(user) for user in users_to_ping))


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

        await asyncio.to_thread(running_activities.backup)


if __name__ == '__main__':

    running_exercises_file = 'running_exercise.jb' 
    running_activities = RunningActivities(running_exercises_file)

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

    # list of known exercise buttons
    # exercise_buttons = ['Discard', 'Hint', 'Correct answer', 'I know this word', 'Answer audio', 'Next', 'Pronounce', 'Easier', 'Harder']
    exercise_buttons = ['Discard', 'Hint', 'Correct answer', 'Answer audio', 'Next', 'Pronounce', 'Easier', 'Harder']

    apps = []
    lang_map = {}

    for bidx, (token, lang) in enumerate(zip(BOT_TOKENS, BOT_LANGS)):
        application = (
            Application.builder()
            .token(token)
            .concurrent_updates(CONCURRENT_UPDATES_LIMIT)
            .build()
        )

        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_request))
        application.add_handler(CommandHandler("add_word", handle_command))
        application.add_handler(CommandHandler("next_test", handle_command))
        application.add_handler(CommandHandler("next_new", handle_command))
        application.add_handler(CommandHandler("song_of_the_day", handle_command))
        application.add_handler(CallbackQueryHandler(handle_inline_request))

        job_queue = application.job_queue
        ping_interval = 15 * 60
        first_ping = nearest_start_time(ping_interval)
        print(f"First ping scheduled in: {first_ping} sec")
        
        job_queue.run_repeating(ping_users, interval=ping_interval, first=first_ping, job_kwargs={'misfire_grace_time': None})
        apps.append(application)
        lang_map[token] = lang

    asyncio.run(run_apps(apps))
