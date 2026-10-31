import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import main

N_THREADS = 8
N_ITERATIONS = 50


@pytest.fixture(autouse=True)
def song_slots(monkeypatch):
    """Each test starts with a clean slot counter and a known limit."""
    monkeypatch.setattr(main, 'song_gen_in_progress', 0)
    monkeypatch.setattr(main, 'SONG_GEN_CONCURRENT_LIMIT', 2)


def make_handler_mocks(monkeypatch, user_data):
    """Patch the globals handle_song_of_the_day relies on; returns the list of sent messages."""
    sent = []

    context = SimpleNamespace(_application=SimpleNamespace(bot=SimpleNamespace(token='token-eng')))
    update = SimpleNamespace(
        message=SimpleNamespace(chat_id=123, text='/song_of_the_day'),
        callback_query=None,
    )

    # these globals live in main's __main__ block, so they may not exist yet (raising=False)
    monkeypatch.setattr(main, 'lang_map', {'token-eng': 'english'}, raising=False)
    monkeypatch.setattr(main, 'user_config', SimpleNamespace(get_user_data=lambda chat_id: user_data), raising=False)
    monkeypatch.setattr(main, 'interface', {
        'Too many song generations are in progress, please try again later': {'english': 'capacity msg'},
        'The next song of the day will be available tomorrow': {'english': 'tomorrow msg'},
    }, raising=False)

    async def fake_send_message(bot, chat_id, text, **kwargs):
        sent.append(text)
    monkeypatch.setattr(main, 'tel_send_message', fake_send_message)

    return update, context, sent


def test_acquire_up_to_limit_then_reject():
    assert main.try_acquire_song_slot() is True
    assert main.try_acquire_song_slot() is True
    assert main.try_acquire_song_slot() is False
    # a rejected attempt must not consume an extra slot
    assert main.song_gen_in_progress == 2


def test_release_frees_slot():
    assert main.try_acquire_song_slot()
    assert main.try_acquire_song_slot()
    assert not main.try_acquire_song_slot()

    main.release_song_slot()
    assert main.song_gen_in_progress == 1
    assert main.try_acquire_song_slot() is True


def test_release_without_acquire_is_safe():
    main.release_song_slot()
    assert main.song_gen_in_progress == 0


def test_slots_never_exceed_limit_under_concurrency(monkeypatch):
    """Concurrent acquirers must never hold more than the limit, and must not leak slots."""
    monkeypatch.setattr(main, 'SONG_GEN_CONCURRENT_LIMIT', 3)
    stats_lock = threading.Lock()
    start = threading.Barrier(N_THREADS)
    current = 0
    peak = 0

    def worker():
        nonlocal current, peak
        start.wait()
        for _ in range(N_ITERATIONS):
            if main.try_acquire_song_slot():
                with stats_lock:
                    current += 1
                    peak = max(peak, current)
                with stats_lock:
                    current -= 1
                main.release_song_slot()

    threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert all(not t.is_alive() for t in threads), "deadlock under concurrent access"
    assert 0 < peak <= 3
    assert main.song_gen_in_progress == 0, "slots leaked"


def test_song_of_the_day_rejects_when_capacity_is_full(monkeypatch):
    update, context, sent = make_handler_mocks(monkeypatch, {'language': 'english', 'timezone': 'UTC'})

    for _ in range(main.SONG_GEN_CONCURRENT_LIMIT):
        assert main.try_acquire_song_slot()

    asyncio.run(main.handle_song_of_the_day(update, context))

    assert sent == ['capacity msg']
    # the rejected request must have left the occupied slots untouched
    assert main.song_gen_in_progress == main.SONG_GEN_CONCURRENT_LIMIT


def test_song_of_the_day_daily_limit_does_not_consume_slot(monkeypatch):
    today = datetime.now(tz=ZoneInfo('UTC')).date().isoformat()
    user_data = {'language': 'english', 'timezone': 'UTC', 'last_song_of_the_day_date': today}
    update, context, sent = make_handler_mocks(monkeypatch, user_data)

    asyncio.run(main.handle_song_of_the_day(update, context))

    assert sent == ['tomorrow msg']
    assert main.song_gen_in_progress == 0


def test_song_of_the_day_with_no_genres_completes(monkeypatch):
    update, context, sent = make_handler_mocks(monkeypatch, {'language': 'english', 'timezone': 'UTC'})
    prompts = []
    saved = []

    async def fake_weather(_):
        return 'sunny'

    async def fake_assistant(_, query, **kwargs):
        prompts.append(query)
        return 'song prompt'

    async def fake_song(*, prompt, dirpath):
        return {
            'title': 'Song', 'lyrics': 'Line', 'img_path': dirpath / 'cover.png',
            'audio_data': [{'audio_file_path': dirpath / 'one.mp3'},
                           {'audio_file_path': dirpath / 'two.mp3'}],
        }

    async def fake_send_media(*args, **kwargs):
        pass

    monkeypatch.setattr(main, 'interface', {
        **main.interface,
        'Thinking': {'english': 'Thinking'},
        'english': {'english': 'English'},
    })
    monkeypatch.setattr(main, 'user_config', SimpleNamespace(
        get_user_data=lambda chat_id: {'language': 'english', 'timezone': 'UTC'},
        set_user_field=lambda *args: saved.append(args)))
    monkeypatch.setattr(main, 'lp', SimpleNamespace(get_due_today=lambda *args: []), raising=False)
    monkeypatch.setattr(main, 'templates', SimpleNamespace(
        get_template=lambda *args: 'Genres: {{ genres }}; words: {{ words_str }}'), raising=False)
    monkeypatch.setattr(main, 'get_weather_forecast', fake_weather)
    monkeypatch.setattr(main, 'get_assistant_response', fake_assistant)
    monkeypatch.setattr(main, 'generate_song_of_the_day', fake_song)
    monkeypatch.setattr(main, 'tel_send_image', fake_send_media)
    monkeypatch.setattr(main, 'tel_send_audio', fake_send_media)

    asyncio.run(main.handle_song_of_the_day(update, context))

    assert prompts == ['Genres: ; words: ']
    assert len(saved) == 1
    assert saved[0][:2] == (123, 'last_song_of_the_day_date')
    assert main.song_gen_in_progress == 0
