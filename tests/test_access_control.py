import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram.ext import Application, ExtBot, TypeHandler

import main
from user_config import UserConfig


@pytest.fixture
def make_config(tmp_path):
    def make(entries):
        path = tmp_path / 'users.json'
        path.write_text(json.dumps({str(user_id): {
            'timezone': 'UTC', 'schedule': {}, **settings,
        } for user_id, settings in entries.items()}), encoding='utf-8')
        return UserConfig(path)
    return make


@pytest.mark.parametrize(('settings', 'allowed'), [
    ({}, True), ({'enabled': True}, True), ({'enabled': False}, False),
    ({'enabled': None}, False), ({'enabled': 'true'}, False), ({'enabled': 1}, False),
])
def test_config_allowlist(make_config, settings, allowed):
    config = make_config({123: settings, -456: {}})
    assert config.is_user_allowed(123) is allowed
    assert config.is_user_allowed(999) is False
    assert config.is_user_allowed(None) is False
    assert config.is_user_allowed(-456) is False
    assert config.is_user_allowed(0) is False


class OfflineBot(ExtBot):
    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    @property
    def username(self):
        return 'flashbot_test'


def make_update(bot, kind, *, chat_id=123, chat_type='private', sender=True):
    user = {'id': 123, 'is_bot': False, 'first_name': 'Alice', 'username': 'alice'}
    message = {'message_id': 1, 'date': 1793440800,
               'chat': {'id': chat_id, 'type': chat_type}}
    if sender:
        message['from'] = user
    if kind == 'button':
        payload = {'callback_query': {'id': 'cb-1', 'from': user,
                   'chat_instance': 'instance', 'message': message, 'data': 'Next'}}
    else:
        if kind.startswith('/'):
            message['text'] = kind
            message['entities'] = [{'type': 'bot_command', 'offset': 0, 'length': len(kind)}]
        elif kind == 'photo':
            message['photo'] = [{'file_id': 'p', 'file_unique_id': 'u', 'width': 1, 'height': 1}]
        else:
            message['text'] = 'hello'
        payload = {'message': message}
    return Update.de_json({'update_id': 1, **payload}, bot)


@pytest.mark.parametrize('kind', [
    '/add_word', '/next_test', '/next_new', '/song_of_the_day',
    'text', 'button', '/unknown', 'photo',
])
@pytest.mark.parametrize('access', ['allowed', 'unknown', 'disabled'])
def test_dispatcher_enforces_access(monkeypatch, make_config, caplog, kind, access):
    config = make_config({} if access == 'unknown' else {123: {'enabled': access == 'allowed'}})
    monkeypatch.setattr(main, 'user_config', config, raising=False)
    handlers = {name: AsyncMock() for name in (
        'handle_command', 'handle_request', 'handle_inline_request')}
    for name, handler in handlers.items():
        monkeypatch.setattr(main, name, handler)
    send, answer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(OfflineBot, 'send_message', send)
    monkeypatch.setattr(OfflineBot, 'answer_callback_query', answer)
    application = Application.builder().bot(OfflineBot('123456:test-token')).updater(None).job_queue(None).build()
    monkeypatch.setattr(main, 'lang_map', {application.bot.token: 'english'}, raising=False)
    monkeypatch.setattr(main, 'interface', {
        'You are not authorized to use this bot': {'english': 'Access denied'},
    }, raising=False)
    main.register_handlers(application)
    downstream = AsyncMock()
    application.add_handler(TypeHandler(Update, downstream), group=1)
    caplog.set_level(logging.INFO, logger='main')

    async def run():
        async with application:
            await application.process_update(make_update(application.bot, kind))

    asyncio.run(run())
    assert any(record.message == 'User interaction' for record in caplog.records)
    expected = ('handle_inline_request' if kind == 'button' else
                'handle_request' if kind == 'text' else
                'handle_command' if kind not in {'/unknown', 'photo'} else None)
    for name, handler in handlers.items():
        assert handler.await_count == int(access == 'allowed' and name == expected)
    assert downstream.await_count == int(access == 'allowed')
    assert send.await_count == int(access != 'allowed' and kind != 'button')
    assert answer.await_count == int(access != 'allowed' and kind == 'button')
    if access != 'allowed':
        assert 'reason=access_denied' in caplog.text


@pytest.mark.parametrize(('kind', 'options', 'notification_fails'), [
    ('text', {'chat_type': 'group', 'chat_id': -456}, False),
    ('button', {'chat_type': 'supergroup', 'chat_id': -456}, False),
    ('text', {'chat_id': 999}, False),
    ('text', {'sender': False}, False),
    ('text', {}, True), ('button', {}, True),
])
def test_denials_stop_all_handlers(monkeypatch, make_config, kind, options, notification_fails):
    config = make_config({} if notification_fails else {123: {}})
    monkeypatch.setattr(main, 'user_config', config, raising=False)
    send = AsyncMock(side_effect=RuntimeError('Telegram unavailable') if notification_fails else None)
    monkeypatch.setattr(OfflineBot, 'send_message', send)
    monkeypatch.setattr(OfflineBot, 'answer_callback_query', send)
    application = Application.builder().bot(OfflineBot('123456:test-token')).updater(None).job_queue(None).build()
    monkeypatch.setattr(main, 'lang_map', {application.bot.token: 'english'}, raising=False)
    monkeypatch.setattr(main, 'interface', {
        'You are not authorized to use this bot': {'english': 'Access denied'},
    }, raising=False)
    downstream = AsyncMock()
    main.register_handlers(application)
    application.add_handler(TypeHandler(Update, downstream), group=1)

    async def run():
        async with application:
            await application.process_update(make_update(application.bot, kind, **options))

    asyncio.run(run())
    downstream.assert_not_awaited()


@pytest.mark.parametrize('access', ['allowed', 'unknown', 'disabled'])
def test_scheduled_generation_requires_access(monkeypatch, make_config, access):
    config = make_config({} if access == 'unknown' else {123: {'enabled': access == 'allowed'}})
    monkeypatch.setattr(main, 'user_config', config, raising=False)
    monkeypatch.setattr(main, 'busy_users', set())
    monkeypatch.setattr(main, 'lang_map', {'token': 'english'}, raising=False)
    exercise = object()
    generate, deliver = AsyncMock(return_value=exercise), AsyncMock()
    monkeypatch.setattr(main, 'lp', SimpleNamespace(get_next_words_exercise=generate), raising=False)
    monkeypatch.setattr(main, 'handle_new_exercise', deliver)
    bot = SimpleNamespace(token='token')
    asyncio.run(main.ping_user(bot, 123, 'english', 'words', 'learn'))
    assert generate.await_count == int(access == 'allowed')
    assert deliver.await_count == int(access == 'allowed')
    assert not main.busy_users
