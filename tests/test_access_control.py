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
    ({}, False), ({'enabled': True}, True), ({'enabled': False}, False),
    ({'enabled': None}, False), ({'enabled': 'true'}, False), ({'enabled': 1}, False),
])
def test_config_allowlist(make_config, settings, allowed):
    config = make_config({123: settings, -456: {'enabled': True}, 0: {'enabled': True}})
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


def make_update(bot, kind, *, chat_id=123, chat_type='private', sender=True, user_id=123):
    user = {'id': user_id, 'is_bot': False, 'first_name': 'Alice', 'username': 'alice'}
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
@pytest.mark.parametrize('access', ['allowed', 'unknown', 'disabled', 'missing_enabled'])
def test_dispatcher_enforces_access(monkeypatch, make_config, caplog, kind, access):
    entries = {999: {'enabled': True}}
    if access != 'unknown':
        entries[123] = {} if access == 'missing_enabled' else {'enabled': access == 'allowed'}
    config = make_config(entries)
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
    ('button', {'chat_id': 999}, False),
    ('text', {'user_id': 999, 'chat_id': 999}, False),
    ('button', {'user_id': 999, 'chat_id': 999}, False),
    ('text', {'user_id': 999}, False),
    ('text', {'chat_type': 'channel', 'chat_id': -456, 'sender': False}, False),
    ('text', {'sender': False}, False),
    ('text', {}, True), ('button', {}, True),
])
def test_denials_stop_all_handlers(monkeypatch, make_config, kind, options, notification_fails):
    config = make_config({} if notification_fails else {123: {'enabled': True}})
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


@pytest.mark.parametrize('kind', ['inline_query', 'inline_button', 'channel_post', 'empty'])
def test_updates_without_private_user_chat_are_denied(monkeypatch, make_config, kind):
    monkeypatch.setattr(main, 'user_config', make_config({123: {'enabled': True}}), raising=False)
    answer = AsyncMock()
    monkeypatch.setattr(OfflineBot, 'answer_callback_query', answer)
    application = Application.builder().bot(OfflineBot('123456:test-token')).updater(None).job_queue(None).build()
    monkeypatch.setattr(main, 'lang_map', {application.bot.token: 'english'}, raising=False)
    monkeypatch.setattr(main, 'interface', {
        'You are not authorized to use this bot': {'english': 'Access denied'},
    }, raising=False)
    downstream = AsyncMock()
    main.register_handlers(application)
    application.add_handler(TypeHandler(Update, downstream), group=1)
    user = {'id': 123, 'is_bot': False, 'first_name': 'Alice'}
    payloads = {
        'inline_query': {'inline_query': {'id': 'iq-1', 'from': user, 'query': '', 'offset': ''}},
        'inline_button': {'callback_query': {'id': 'cb-1', 'from': user,
                          'chat_instance': 'instance', 'inline_message_id': 'im-1', 'data': 'Next'}},
        'channel_post': {'channel_post': {'message_id': 1, 'date': 1793440800,
                         'chat': {'id': -456, 'type': 'channel', 'title': 'Channel'}, 'text': 'hello'}},
        'empty': {},
    }

    async def run():
        async with application:
            await application.process_update(Update.de_json({'update_id': 1, **payloads[kind]}, application.bot))

    asyncio.run(run())
    downstream.assert_not_awaited()
    assert answer.await_count == int(kind == 'inline_button')


@pytest.mark.parametrize('kind', ['/next_new', 'text', 'button'])
def test_access_check_failure_stops_all_handlers(monkeypatch, make_config, kind):
    config = make_config({123: {'enabled': True}})

    def broken_access_check(user_id):
        raise RuntimeError('User configuration unavailable')

    monkeypatch.setattr(config, 'is_user_allowed', broken_access_check)
    monkeypatch.setattr(main, 'user_config', config, raising=False)
    handlers = [AsyncMock() for _ in range(3)]
    for name, handler in zip(('handle_command', 'handle_request', 'handle_inline_request'), handlers):
        monkeypatch.setattr(main, name, handler)
    monkeypatch.setattr(OfflineBot, 'send_message', AsyncMock())
    monkeypatch.setattr(OfflineBot, 'answer_callback_query', AsyncMock())
    application = Application.builder().bot(OfflineBot('123456:test-token')).updater(None).job_queue(None).build()
    monkeypatch.setattr(main, 'lang_map', {application.bot.token: 'english'}, raising=False)
    monkeypatch.setattr(main, 'interface', {
        'You are not authorized to use this bot': {'english': 'Access denied'},
    }, raising=False)
    main.register_handlers(application)
    downstream = AsyncMock()
    application.add_handler(TypeHandler(Update, downstream), group=1)

    async def run():
        async with application:
            await application.process_update(make_update(application.bot, kind))

    asyncio.run(run())
    for handler in [*handlers, downstream]:
        handler.assert_not_awaited()


@pytest.mark.parametrize('access', ['allowed', 'unknown', 'disabled', 'missing_enabled'])
def test_scheduled_generation_requires_access(monkeypatch, make_config, access):
    entries = {999: {'enabled': True}}
    if access != 'unknown':
        entries[123] = {} if access == 'missing_enabled' else {'enabled': access == 'allowed'}
    config = make_config(entries)
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
