import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import main


@pytest.mark.parametrize(('utc_now', 'expected'), [
    ('2026-10-30T23:00:00+00:00', {1: 'learn', 2: 'test'}),
    ('2026-10-31T00:00:00+00:00', {1: 'learn', 2: 'test'}),
    ('2026-11-01T23:00:00+00:00', {1: 'test', 2: 'learn'}),
    ('2026-11-02T00:00:00+00:00', {1: 'test', 2: 'learn'}),
])
def test_scheduled_pings_use_each_users_local_weekday(monkeypatch, utc_now, expected):
    fixed_now = datetime.fromisoformat(utc_now)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # Model a UTC server, independent of the machine running the test.
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    users = {}
    for chat_id, timezone in ((1, 'America/Los_Angeles'), (2, 'Asia/Tokyo')):
        local_time = fixed_now.astimezone(ZoneInfo(timezone)).timetz()
        users[chat_id] = {
            'enabled': True,
            'ui_language': 'english', 'language': 'english', 'timezone': timezone,
            'exercise_types': ['words'],
            'schedule': {'words': {
                'weekday': {local_time: 'learn'},
                'weekend': {local_time: 'test'},
            }},
        }

    # Entries without explicit boolean permission and group chats are skipped.
    users[3] = {'enabled': False}
    users[-4] = {'enabled': True}
    users[5] = {}
    users[6] = {'enabled': None}
    users[7] = {'enabled': 'true'}
    users[8] = {'enabled': 1}
    sent = {}

    async def record_ping(bot, chat_id, lang, exercise_type, exercise_data):
        sent[chat_id] = exercise_data

    monkeypatch.setattr(main, 'datetime', FrozenDateTime)
    monkeypatch.setattr(main, 'user_config', SimpleNamespace(get_all_user_data=lambda: users), raising=False)
    monkeypatch.setattr(main, 'lang_map', {'token-eng': 'english'}, raising=False)
    monkeypatch.setattr(main, 'ping_user', record_ping)
    context = SimpleNamespace(_application=SimpleNamespace(bot=SimpleNamespace(token='token-eng')))

    asyncio.run(main.ping_users(context))

    assert sent == expected
