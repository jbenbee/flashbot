import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, time as clock_time
from types import SimpleNamespace
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import main
from user_config import UserConfig
from utils import download_file


def test_media_read_does_not_block_other_requests(tmp_path, monkeypatch):
    audio_path = tmp_path / 'song.mp3'
    audio_path.write_bytes(b'audio data')
    original_read_bytes = Path.read_bytes
    sent = []

    def slow_read_bytes(path):
        time.sleep(0.05)
        return original_read_bytes(path)

    async def send_audio(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(Path, 'read_bytes', slow_read_bytes)
    bot = SimpleNamespace(send_audio=send_audio)

    async def run():
        upload = asyncio.create_task(main.tel_send_audio(bot, 123, audio_path))
        await asyncio.sleep(0.01)
        assert not upload.done()
        await upload

    asyncio.run(run())

    assert sent[0]['audio'].input_file_content == b'audio data'
    assert sent[0]['audio'].filename == 'song.mp3'


def test_song_download_writes_streamed_chunks(tmp_path):
    class FakeResponse:
        def raise_for_status(self):
            pass

        async def aiter_bytes(self, chunk_size):
            yield b'first'
            yield b'second'

    class FakeClient:
        @asynccontextmanager
        async def stream(self, method, url, headers):
            assert method == 'GET'
            yield FakeResponse()

    destination = tmp_path / 'song.mp3'
    asyncio.run(download_file(FakeClient(), 'https://example.test/song.mp3', destination))

    assert destination.read_bytes() == b'firstsecond'


def test_scheduled_pings_start_for_multiple_users(monkeypatch):
    utc = ZoneInfo('UTC')
    fixed_now = datetime(2026, 9, 28, 12, 0, tzinfo=utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    schedule = {'words': {'weekday': {clock_time(12, 0, tzinfo=utc): 'learn'}, 'weekend': {}}}
    users = {chat_id: {
        'ui_language': 'english', 'timezone': 'UTC', 'language': 'english',
        'exercise_types': ['words'], 'schedule': schedule,
    } for chat_id in (1, 2)}
    started = []
    both_started = asyncio.Event()

    async def fake_ping_user(bot, chat_id, lang, exercise_type, exercise_data):
        started.append(chat_id)
        if len(started) == 2:
            both_started.set()
        await both_started.wait()

    monkeypatch.setattr(main, 'datetime', FrozenDateTime)
    monkeypatch.setattr(main, 'CONCURRENT_UPDATES_LIMIT', 2)
    monkeypatch.setattr(main, 'user_config', SimpleNamespace(get_all_user_data=lambda: users), raising=False)
    monkeypatch.setattr(main, 'lang_map', {'token-eng': 'english'}, raising=False)
    monkeypatch.setattr(main, 'ping_user', fake_ping_user)
    context = SimpleNamespace(_application=SimpleNamespace(bot=SimpleNamespace(token='token-eng')))

    asyncio.run(asyncio.wait_for(main.ping_users(context), timeout=1))

    assert set(started) == {1, 2}


@pytest.mark.parametrize('handler_name', [
    'handle_new_exercise', 'handle_next_new', 'handle_next_test',
    'handle_song_of_the_day', 'handle_inline_request', 'handle_request',
])
def test_config_save_does_not_block_event_loop(tmp_path, monkeypatch, running_activities, handler_name):
    today = datetime.now(tz=ZoneInfo('UTC')).date().isoformat()
    users = {str(chat_id): {
        'language': 'english', 'ui_language': 'english', 'timezone': 'UTC',
        'schedule': {'words': {'weekday': {}, 'weekend': {}}},
        'last_song_of_the_day_date': today,
    } for chat_id in (42, 99)}
    config_path = tmp_path / 'users.json'
    config_path.write_text(json.dumps(users), encoding='utf-8')
    config = UserConfig(config_path)
    save_started = threading.Event()
    read_started = threading.Event()
    release_save = threading.Event()
    save_timed_out = threading.Event()
    original_dump = json.dump
    original_get_user_data = config.get_user_data
    sent = []

    def slow_dump(*args, **kwargs):
        save_started.set()
        # A timeout lets a broken handler fail instead of hanging the suite.
        if not release_save.wait(timeout=2):
            save_timed_out.set()
        return original_dump(*args, **kwargs)

    def get_user_data(chat_id):
        read_started.set()
        return original_get_user_data(chat_id)

    async def send_message(bot, chat_id, text, **kwargs):
        sent.append(text)

    async def next_exercise(*args, **kwargs):
        return None

    async def exercise_message(**kwargs):
        return 'exercise', None

    monkeypatch.setattr('user_config.json.dump', slow_dump)
    monkeypatch.setattr(config, 'get_user_data', get_user_data)
    monkeypatch.setattr(main, 'user_config', config, raising=False)
    monkeypatch.setattr(main, 'running_activities', running_activities, raising=False)
    monkeypatch.setattr(main, 'busy_users', set())
    monkeypatch.setattr(main, 'lang_map', {'token-eng': 'english'}, raising=False)
    monkeypatch.setattr(main, 'exercise_buttons', ['Next'], raising=False)
    monkeypatch.setattr(main, 'lp', SimpleNamespace(get_next_words_exercise=next_exercise), raising=False)
    messages = [
        'Thinking', 'Could not create an exercise, please try again later',
        'The next song of the day will be available tomorrow',
        'Sorry, the exercise has been completed or is expired',
        'No exercises or commands are running, this message will be ignored',
        'Something went terribly wrong, please try again or notify the admin',
    ]
    monkeypatch.setattr(main, 'interface', {msg: {'english': msg} for msg in messages}, raising=False)
    monkeypatch.setattr(main, 'tel_send_message', send_message)
    bot = SimpleNamespace(token='token-eng')
    context = SimpleNamespace(_application=SimpleNamespace(bot=bot))
    update = SimpleNamespace(
        message=SimpleNamespace(chat_id=42, text='hello'),
        callback_query=SimpleNamespace(from_user=SimpleNamespace(id=42), data='Next'),
    )

    async def run():
        writer = asyncio.create_task(asyncio.to_thread(config.set_user_field, 99, 'level', 'A2'))
        request = None
        try:
            assert await asyncio.to_thread(save_started.wait, 2)
            if handler_name == 'handle_new_exercise':
                handler = main.handle_new_exercise(bot, 42, SimpleNamespace(get_next_user_message=exercise_message))
            else:
                handler = getattr(main, handler_name)(update, context)
            request = asyncio.create_task(handler)
            assert await asyncio.to_thread(read_started.wait, 2)
            # We can run on the event loop while the request waits for the save's lock.
            assert not save_timed_out.is_set(), 'config read blocked the event loop'
            assert not writer.done()
            assert not request.done()
        finally:
            release_save.set()
            await writer
            if request is not None:
                await request

    asyncio.run(run())

    assert sent
    assert not any('Something went terribly wrong' in message for message in sent)
    assert json.loads(config_path.read_text(encoding='utf-8'))['99']['level'] == 'A2'
