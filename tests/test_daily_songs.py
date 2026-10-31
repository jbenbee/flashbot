import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main
from templates import Templates
from user_config import UserConfig


@pytest.fixture
def daily_songs(monkeypatch, tmp_path):
    fixed_now = datetime.fromisoformat('2026-10-31T12:00:00+00:00')

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(main, 'datetime', FrozenDateTime)
    monkeypatch.setattr(main, 'busy_users', set())
    monkeypatch.setattr(main, 'song_gen_in_progress', 0)
    monkeypatch.setattr(main, 'SONG_GEN_CONCURRENT_LIMIT', 2)
    monkeypatch.setattr(main, 'lang_map', {'token': 'english'}, raising=False)
    monkeypatch.setattr(main, 'interface', {
        'Thinking': {'english': 'Thinking'},
        'english': {'english': 'English'},
        'The next song of the day will be available tomorrow': {'english': 'tomorrow'},
    }, raising=False)
    monkeypatch.setattr(main, 'templates', Templates('resources/templates'), raising=False)
    monkeypatch.setattr(main, 'lp', SimpleNamespace(get_due_today=lambda *args: []), raising=False)
    monkeypatch.setattr(main, 'get_weather_forecast', AsyncMock(return_value='sunny'))
    monkeypatch.setattr(main, 'get_assistant_response', AsyncMock(return_value='A song idea'))
    for name in ('tel_send_message', 'tel_send_image', 'tel_send_audio'):
        monkeypatch.setattr(main, name, AsyncMock())

    async def generate(*, prompt, dirpath, style=None):
        return {
            'title': 'Song', 'img_path': dirpath / 'cover.png',
            'audio_data': [{'audio_file_path': dirpath / 'one.mp3', 'title': 'Song', 'lyrics': 'First lyrics'},
                           {'audio_file_path': dirpath / 'two.mp3', 'title': 'Other song', 'lyrics': 'Second lyrics'}],
        }

    monkeypatch.setattr(main, 'generate_song_of_the_day', AsyncMock(side_effect=generate))
    context = SimpleNamespace(_application=SimpleNamespace(bot=SimpleNamespace(token='token')))
    path = tmp_path / 'users.json'

    def configure(entries):
        users = {str(chat_id): {
            'enabled': True, 'song_of_the_day': True,
            'timezone': 'Europe/Rome', 'ui_language': 'english',
            'language': 'english', 'genres': ['jazz', 'rock'], 'schedule': {},
            **settings,
        } for chat_id, settings in entries.items()}
        path.write_text(json.dumps(users), encoding='utf-8')
        config = UserConfig(path)
        monkeypatch.setattr(main, 'user_config', config, raising=False)
        return config

    return SimpleNamespace(configure=configure, context=context, path=path)


def test_daily_delivery_persists_and_shares_limit_with_manual_requests(daily_songs, monkeypatch):
    config = daily_songs.configure({123: {}})
    update = SimpleNamespace(message=SimpleNamespace(chat_id=123))

    async def run():
        await main.send_daily_songs(daily_songs.context)
        # Reload from disk to model a restart, then try both delivery paths.
        monkeypatch.setattr(main, 'user_config', UserConfig(daily_songs.path))
        await main.send_daily_songs(daily_songs.context)
        await main.handle_song_of_the_day(update, daily_songs.context)

    asyncio.run(run())

    main.generate_song_of_the_day.assert_awaited_once()
    assert main.tel_send_image.await_count == 1
    assert main.tel_send_audio.await_count == 2
    assert config.get_user_data(123)['last_song_of_the_day_date'] == '2026-10-31'
    assert main.tel_send_message.await_args.args[2] == 'tomorrow'
    assert not main.busy_users
    assert main.song_gen_in_progress == 0


def test_manual_delivery_prevents_automatic_duplicate(daily_songs):
    daily_songs.configure({123: {}})

    async def run():
        await main.handle_song_of_the_day(
            SimpleNamespace(message=SimpleNamespace(chat_id=123)), daily_songs.context)
        await main.send_daily_songs(daily_songs.context)

    asyncio.run(run())
    main.generate_song_of_the_day.assert_awaited_once()


@pytest.mark.parametrize('scheduled', [False, True])
@pytest.mark.parametrize('track_count', [1, 2])
def test_each_track_is_sent_with_its_own_title_and_lyrics(daily_songs, scheduled, track_count):
    daily_songs.configure({123: {'genres': ['jazz']}})
    generate = main.generate_song_of_the_day.side_effect
    sent = []

    async def generate_tracks(**kwargs):
        result = await generate(**kwargs)
        result['audio_data'] = result['audio_data'][:track_count]
        return result

    async def send_message(bot, chat_id, text):
        if text != 'Thinking...':
            sent.append(('lyrics', text))

    async def send_audio(bot, chat_id, path, *, title):
        sent.append(('audio', path.name, title))

    main.generate_song_of_the_day.side_effect = generate_tracks
    main.tel_send_message.side_effect = send_message
    main.tel_send_audio.side_effect = send_audio

    if scheduled:
        asyncio.run(main.send_daily_songs(daily_songs.context))
    else:
        update = SimpleNamespace(message=SimpleNamespace(chat_id=123))
        asyncio.run(main.handle_song_of_the_day(update, daily_songs.context))

    title = 'Song (1)' if track_count > 1 else 'Song'
    expected = [('lyrics', f'{title}\n\n> First lyrics'), ('audio', 'one.mp3', title)]
    if track_count > 1:
        expected += [('lyrics', 'Other song (2)\n\n> Second lyrics'),
                     ('audio', 'two.mp3', 'Other song (2)')]
    assert sent == expected
    assert main.generate_song_of_the_day.await_args.kwargs['style'] == 'jazz'


@pytest.mark.parametrize('lyrics', ['A line\n' * 900, '_*!😀' * 1500])
def test_long_song_lyrics_fit_telegram_and_preserve_all_text(monkeypatch, lyrics):
    send = AsyncMock()
    monkeypatch.setattr(main, 'tel_send_message', send)

    asyncio.run(main.tel_send_song_lyrics(None, 123, 'Song (2)', lyrics))

    chunks = []
    assert send.await_count > 1
    for call in send.await_args_list:
        message = call.args[2]
        assert message.startswith('Song (2)\n\n')
        converted = main.telegramify_markdown.markdownify(message)
        assert len(converted.encode('utf-16-le')) // 2 <= 4096
        chunks.append('\n'.join(line.removeprefix('> ') for line in message.split('\n\n', 1)[1].split('\n')))
    assert ''.join(chunks) == lyrics


@pytest.mark.parametrize(('settings', 'due'), [
    ({}, True),
    ({'timezone': 'America/Los_Angeles'}, False),
    ({'song_of_the_day_time': '13:01'}, False),
    ({'song_of_the_day_time': '13:00'}, True),
    ({'last_song_of_the_day_date': '2026-10-30'}, True),
    ({'last_song_of_the_day_date': '2026-10-31'}, False),
    # Kiritimati is already on the next calendar day.
    ({'timezone': 'Pacific/Kiritimati', 'song_of_the_day_time': '01:00',
      'last_song_of_the_day_date': '2026-10-31'}, True),
    ({'timezone': 'Pacific/Kiritimati', 'song_of_the_day_time': '03:00'}, False),
    ({'song_of_the_day': False}, False),
    ({'song_of_the_day': None}, False),
    ({'song_of_the_day': 'true'}, False),
    ({'song_of_the_day': 1}, False),
    ({'enabled': False}, False),
    ({'enabled': None}, False),
    ({'enabled': 'true'}, False),
    ({'enabled': 1}, False),
    ({'ui_language': 'russian'}, False),
    ({'genres': []}, False),
])
def test_daily_delivery_eligibility_uses_local_time(daily_songs, settings, due):
    daily_songs.configure({123: settings, -456: {}, 0: {}})
    asyncio.run(main.send_daily_songs(daily_songs.context))
    assert main.generate_song_of_the_day.await_count == int(due)


def test_daily_delivery_is_opt_in(daily_songs):
    config = daily_songs.configure({123: {}})
    # Missing settings must not opt existing users in.
    del config._user_data[123]['song_of_the_day']
    asyncio.run(main.send_daily_songs(daily_songs.context))
    main.generate_song_of_the_day.assert_not_awaited()


@pytest.mark.parametrize('words', [[], ['hello', 'world']])
def test_both_prompts_use_one_selected_genre_even_without_due_words(daily_songs, monkeypatch, words):
    daily_songs.configure({123: {}})
    monkeypatch.setattr(main.lp, 'get_due_today', lambda *args: words)
    monkeypatch.setattr(main.random, 'choice', lambda genres: genres[1])
    asyncio.run(main.send_daily_songs(daily_songs.context))

    idea_prompt = main.get_assistant_response.await_args.args[1]
    song_prompt = main.generate_song_of_the_day.await_args.kwargs['prompt']
    for prompt in (idea_prompt, song_prompt):
        assert 'rock genre' in prompt
        assert 'jazz' not in prompt
    for word in words:
        assert word in idea_prompt


@pytest.mark.parametrize('scheduled', [False, True])
@pytest.mark.parametrize('words', [[], ['hello', 'world']])
def test_personal_info_reaches_both_prompts_for_the_correct_user(daily_songs, monkeypatch, scheduled, words):
    personal_info = 'I like cakes.\nI enjoy hiking in the mountains.'
    daily_songs.configure({
        123: {'personal_info': personal_info},
        456: {'personal_info': 'I enjoy sailing.', 'song_of_the_day': False},
    })
    monkeypatch.setattr(main.lp, 'get_due_today', lambda *args: words)
    if scheduled:
        asyncio.run(main.send_daily_songs(daily_songs.context))
    else:
        asyncio.run(main.handle_song_of_the_day(
            SimpleNamespace(message=SimpleNamespace(chat_id=123)), daily_songs.context))

    main.generate_song_of_the_day.assert_awaited_once()
    idea_prompt = main.get_assistant_response.await_args.args[1]
    song_prompt = main.generate_song_of_the_day.await_args.kwargs['prompt']
    for prompt in (idea_prompt, song_prompt):
        assert 'I like cakes.' in prompt
        assert 'I enjoy hiking in the mountains.' in prompt
        assert 'sailing' not in prompt
    for word in words:
        assert word in idea_prompt


@pytest.mark.parametrize('personal_info', ['', '  \n ', None])
def test_empty_personal_info_skips_personalization(daily_songs, personal_info):
    daily_songs.configure({123: {'personal_info': personal_info}})
    asyncio.run(main.send_daily_songs(daily_songs.context))
    main.generate_song_of_the_day.assert_awaited_once()
    idea_prompt = main.get_assistant_response.await_args.args[1]
    song_prompt = main.generate_song_of_the_day.await_args.kwargs['prompt']
    assert 'Listener background:' not in idea_prompt
    assert 'Listener background:' not in song_prompt


def test_busy_users_and_full_capacity_are_retried_silently(daily_songs):
    config = daily_songs.configure({123: {}})

    async def run():
        assert main.try_acquire_user_slot(123)
        await main.send_daily_songs(daily_songs.context)
        main.release_user_slot(123)
        assert main.try_acquire_song_slot()
        assert main.try_acquire_song_slot()
        await main.send_daily_songs(daily_songs.context)
        main.generate_song_of_the_day.assert_not_awaited()
        main.tel_send_message.assert_not_awaited()
        assert 'last_song_of_the_day_date' not in config.get_user_data(123)
        main.release_song_slot()
        main.release_song_slot()
        await main.send_daily_songs(daily_songs.context)

    asyncio.run(run())
    main.generate_song_of_the_day.assert_awaited_once()
    assert not main.busy_users
    assert main.song_gen_in_progress == 0


def test_all_due_users_are_served_with_bounded_concurrency(daily_songs):
    daily_songs.configure({chat_id: {} for chat_id in range(1, 6)})
    generate = main.generate_song_of_the_day.side_effect
    peak = 0

    async def slow_generate(**kwargs):
        nonlocal peak
        peak = max(peak, main.song_gen_in_progress)
        await asyncio.sleep(0.01)
        return await generate(**kwargs)

    main.generate_song_of_the_day.side_effect = slow_generate
    asyncio.run(main.send_daily_songs(daily_songs.context))
    assert main.generate_song_of_the_day.await_count == 5
    assert peak == main.SONG_GEN_CONCURRENT_LIMIT
    assert not main.busy_users
    assert main.song_gen_in_progress == 0


def test_manual_command_cannot_race_scheduled_generation(daily_songs, monkeypatch):
    daily_songs.configure({123: {}})
    generate = main.generate_song_of_the_day.side_effect
    monkeypatch.setattr(main, 'notify_user_is_busy', AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(chat_id=123, text='/song_of_the_day'))

    async def run():
        started, finish = asyncio.Event(), asyncio.Event()

        async def slow_generate(**kwargs):
            started.set()
            await finish.wait()
            return await generate(**kwargs)

        main.generate_song_of_the_day.side_effect = slow_generate
        delivery = asyncio.create_task(main.send_daily_songs(daily_songs.context))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            await main.handle_command(update, daily_songs.context)
            main.notify_user_is_busy.assert_awaited_once()
        finally:
            finish.set()
            await delivery

    asyncio.run(run())
    main.generate_song_of_the_day.assert_awaited_once()
    assert not main.busy_users


def test_failed_delivery_is_retried_and_does_not_block_other_users(daily_songs):
    config = daily_songs.configure({123: {}, 456: {}})
    main.tel_send_audio.side_effect = RuntimeError('Telegram unavailable')

    async def run():
        await main.send_daily_songs(daily_songs.context)
        for chat_id in (123, 456):
            assert 'last_song_of_the_day_date' not in config.get_user_data(chat_id)
        assert not main.busy_users
        assert main.song_gen_in_progress == 0
        main.tel_send_audio.side_effect = None
        await main.send_daily_songs(daily_songs.context)

    asyncio.run(run())
    assert main.generate_song_of_the_day.await_count == 4
    for chat_id in (123, 456):
        assert config.get_user_data(chat_id)['last_song_of_the_day_date'] == '2026-10-31'


def test_bad_settings_do_not_block_other_users(daily_songs):
    daily_songs.configure({123: {'song_of_the_day_time': 'bad'}, 456: {}})
    asyncio.run(main.send_daily_songs(daily_songs.context))
    main.generate_song_of_the_day.assert_awaited_once()
