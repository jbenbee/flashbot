import asyncio
import csv
import logging
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram.ext import Application, ExtBot

import logging_config
from logging_config import CSV_FIELDS, configure_logging, logged_operation
import main


def read_rows(path):
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        assert reader.fieldnames == list(CSV_FIELDS)
        rows = list(reader)
    assert all(set(row) == set(CSV_FIELDS) and None not in row.values() for row in rows)
    return rows


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    root = logging.getLogger()
    previous_level = root.level
    previous_library_levels = {
        name: logging.getLogger(name).level
        for name in ('httpx', 'httpcore', 'openai', 'telegram', 'apscheduler', 'websockets')
    }
    for key in ('LEVEL', 'MAX_BYTES', 'BACKUP_COUNT', 'CONSOLE', 'CONSOLE_USER_ONLY'):
        monkeypatch.delenv(f'FLASHBOT_LOG_{key}', raising=False)
    monkeypatch.setenv('FLASHBOT_LOG_DIR', str(tmp_path / 'logs'))
    # Leave pytest's warning capture in control during tests.
    monkeypatch.setattr(logging, 'captureWarnings', lambda enabled: None)
    yield tmp_path / 'logs'
    for handler in root.handlers[:]:
        if getattr(handler, '_flashbot_handler', False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(previous_level)
    for name, level in previous_library_levels.items():
        logging.getLogger(name).setLevel(level)


def test_default_directory_is_relative_to_repo(log_dir, tmp_path, monkeypatch):
    monkeypatch.delenv('FLASHBOT_LOG_DIR')
    monkeypatch.setattr(logging_config, '__file__', str(tmp_path / 'checkout' / 'logging_config.py'))
    monkeypatch.chdir(tmp_path)

    path = configure_logging()

    assert path == tmp_path / 'checkout' / 'logs' / 'flashbot.csv'
    handler = next(h for h in logging.getLogger().handlers if getattr(h, '_flashbot_handler', False))
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 3
    assert len([h for h in logging.getLogger().handlers if getattr(h, '_flashbot_handler', False)]) == 1
    assert 'Logging configured' in path.read_text()


def test_rotation_bounds_utf8_and_oversized_events(log_dir, monkeypatch):
    monkeypatch.setenv('FLASHBOT_LOG_MAX_BYTES', '1024')
    monkeypatch.setenv('FLASHBOT_LOG_BACKUP_COUNT', '2')
    path = configure_logging()
    logger = logging.getLogger('rotation-test')
    logger.info('oldest-event')
    for i in range(30):
        logger.info('event=%s %s', i, '界' * 5000)
    logger.info('newest-event')

    files = list(log_dir.iterdir())
    assert {p.name for p in files} == {'flashbot.csv', 'flashbot.1.csv', 'flashbot.2.csv'}
    assert all(p.stat().st_size <= 1024 for p in files)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    assert log_dir.stat().st_mode & 0o777 == 0o700
    assert 'newest-event' in path.read_text()
    retained = ''.join(p.read_text() for p in files)
    assert 'oldest-event' not in retained
    assert '[truncated]' in retained
    for file in files:
        assert read_rows(file)
        assert file.read_text().count(',message,exception\n') == 1


def test_csv_round_trips_quoted_metadata_and_redacts_every_column(log_dir, monkeypatch, capsys):
    monkeypatch.setenv('SUNO_API_KEY', 'metadata-secret-value')
    monkeypatch.setenv('FLASHBOT_LOG_CONSOLE', '1')
    path = configure_logging()
    logger = logging.getLogger('csv-test')
    action = 'A comma, a "quote", and 日本語\nnext line'
    try:
        raise ValueError('service, "failure"\nmetadata-secret-value')
    except ValueError:
        logger.exception('message, "quoted"\nsecond line', extra={
            'event': 'user_interaction', 'kind': 'button', 'user_id': 321,
            'username': 'metadata-secret-value', 'chat_id': 123, 'update_id': 777,
            'action': action,
        })

    row = read_rows(path)[-1]
    assert row['timestamp'].endswith('Z')
    assert row['level'] == 'ERROR'
    assert row['event'] == 'user_interaction'
    assert row['user_id'] == '321'
    assert row['chat_id'] == '123'
    assert row['username'] == '[REDACTED]'
    assert row['action'] == action.replace('\n', r'\n')
    assert row['message'] == 'message, "quoted"\\nsecond line'
    assert 'Traceback' in row['exception']
    assert 'ValueError: service, "failure"\\n[REDACTED]' in row['exception']
    assert len(path.read_text().splitlines()) == 3
    console = capsys.readouterr().err
    assert 'user_id=321' in console and 'chat_id=123' in console
    assert 'metadata-secret-value' not in console


def test_csv_truncation_keeps_rows_parseable_after_quote_expansion(log_dir, monkeypatch):
    monkeypatch.setenv('FLASHBOT_LOG_MAX_BYTES', '1024')
    path = configure_logging()
    giant_cell = '日本語,"' * 3000
    logger = logging.getLogger('csv-test')
    try:
        raise ValueError(giant_cell)
    except ValueError:
        logger.exception(giant_cell, extra={
            'event': 'user_interaction', 'kind': 'button', 'action': giant_cell,
        })

    for file in log_dir.glob('*.csv'):
        assert file.stat().st_size <= 1024
        read_rows(file)
    row = read_rows(path)[-1]
    assert row['event'] == 'user_interaction'
    assert '[truncated]' in row['message']
    assert '[truncated]' in row['action']
    assert '[truncated]' in row['exception']


def test_csv_header_is_not_duplicated_on_restart_or_when_console_is_disabled(log_dir, monkeypatch):
    monkeypatch.setenv('FLASHBOT_LOG_LEVEL', 'ERROR')
    path = configure_logging()
    assert read_rows(path) == []
    logging.getLogger('csv-test').error('before-restart')
    configure_logging()
    logging.getLogger('csv-test').error('after-restart')

    assert [row['message'] for row in read_rows(path)] == ['before-restart', 'after-restart']
    assert len(path.read_text().splitlines()) == 3


def test_credentials_redacted_in_file_console_and_traceback(log_dir, monkeypatch, capsys):
    token = '123456789:abcdefghijklmnopqrstuvwxyz0123456789'
    key = 'configured-suno-secret'
    monkeypatch.setenv('BOT_TOKEN_ENG', token)
    monkeypatch.setenv('SUNO_API_KEY', key)
    monkeypatch.setenv('FLASHBOT_LOG_CONSOLE', '1')
    path = configure_logging()
    try:
        raise RuntimeError(f'https://api.telegram.org/bot{token}/sendMessage {key}')
    except RuntimeError:
        logging.getLogger('secret-test').exception(
            'Failed api_key=%s Bearer %s https://example.test/?api_key=%s\nforged-line',
            'sk-unknownOpenAISecret', 'unknownBearerSecret', 'unknownQuerySecret')

    for output in (path.read_text(), capsys.readouterr().err):
        for secret in (token, key, 'sk-unknownOpenAISecret', 'unknownBearerSecret', 'unknownQuerySecret'):
            assert secret not in output
        assert '[REDACTED]' in output
        assert 'Traceback' in output
        assert r'\nforged-line' in output
    assert len(read_rows(path)) == 2
    assert len(path.read_text().splitlines()) == 3
    assert len(output.splitlines()) == 2


def test_reconfiguration_does_not_duplicate_handlers(log_dir, monkeypatch):
    configure_logging()
    monkeypatch.setenv('FLASHBOT_LOG_LEVEL', 'DEBUG')
    path = configure_logging()
    logging.getLogger('app-test').debug('one-debug-event')
    logging.getLogger('httpx').info('no-http-url-logging')
    logging.getLogger('openai').debug('no-sdk-payload-logging')
    logging.getLogger('httpcore').warning('transport-warning')

    output = path.read_text()
    assert output.count('one-debug-event') == 1
    assert 'no-http-url-logging' not in output
    assert 'no-sdk-payload-logging' not in output
    assert 'transport-warning' in output
    assert any(row['event'] == 'application' and row['message'] == 'transport-warning' for row in read_rows(path))
    monkeypatch.setenv('FLASHBOT_LOG_LEVEL', 'ERROR')
    configure_logging()
    logging.getLogger('httpcore').warning('below-configured-severity')
    assert 'below-configured-severity' not in path.read_text()


def test_console_can_show_only_interactions_while_file_keeps_all_logs(log_dir, monkeypatch, capsys):
    monkeypatch.setenv('FLASHBOT_LOG_CONSOLE', '1')
    monkeypatch.setenv('FLASHBOT_LOG_CONSOLE_USER_ONLY', '1')
    path = configure_logging()
    logger = logging.getLogger('filter-test')
    logger.info('scheduled-ping')
    logger.error('service-failure')
    # Filtering must use the structured field, not a substring in the message.
    logger.info('not-an-interaction event=user_interaction')
    logger.info('button-click', extra={'event': 'user_interaction'})

    console = capsys.readouterr().err
    assert 'event=user_interaction button-click' in console
    assert len(console.splitlines()) == 1
    output = path.read_text()
    events = {row['message']: row['event'] for row in read_rows(path)}
    assert events['scheduled-ping'] == 'application'
    assert events['service-failure'] == 'application'
    assert events['not-an-interaction event=user_interaction'] == 'application'
    assert events['button-click'] == 'user_interaction'

    # Restart/reconfiguration can restore normal console output.
    monkeypatch.setenv('FLASHBOT_LOG_CONSOLE_USER_ONLY', '0')
    configure_logging()
    logger.info('normal-console-restored')
    assert 'event=application normal-console-restored' in capsys.readouterr().err


def test_user_only_setting_does_not_enable_console_or_filter_files(log_dir, monkeypatch, capsys):
    monkeypatch.setenv('FLASHBOT_LOG_CONSOLE_USER_ONLY', '1')
    path = configure_logging()
    logger = logging.getLogger('filter-test')
    logger.info('normal-file-event')
    logger.info('interaction-file-event', extra={'event': 'user_interaction'})

    assert capsys.readouterr().err == ''
    output = path.read_text()
    events = {row['message']: row['event'] for row in read_rows(path)}
    assert events['normal-file-event'] == 'application'
    assert events['interaction-file-event'] == 'user_interaction'


@pytest.mark.parametrize(('key', 'value'), [
    ('LEVEL', 'verbose'), ('MAX_BYTES', '0'), ('MAX_BYTES', 'invalid'),
    ('BACKUP_COUNT', '0'), ('BACKUP_COUNT', '-1'), ('CONSOLE', 'maybe'),
    ('CONSOLE_USER_ONLY', 'maybe'),
])
def test_invalid_settings_fail_before_creating_logs(log_dir, monkeypatch, key, value):
    monkeypatch.setenv(f'FLASHBOT_LOG_{key}', value)
    with pytest.raises(ValueError):
        configure_logging()
    assert not log_dir.exists()


def test_async_operations_log_outcomes_without_arguments_or_results(log_dir):
    path = configure_logging()

    @logged_operation
    async def succeed(prompt):
        return 'private-result'

    @logged_operation
    async def fail():
        raise RuntimeError('test-service-failure')

    @logged_operation
    async def cancel():
        raise asyncio.CancelledError()

    assert asyncio.run(succeed('private-prompt')) == 'private-result'
    with pytest.raises(RuntimeError):
        asyncio.run(fail())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancel())

    output = path.read_text()
    assert 'Operation completed operation=succeed' in output
    assert 'Operation failed operation=fail' in output
    assert 'Operation cancelled operation=cancel' in output
    assert 'Traceback' in output
    assert 'duration_seconds=' in output
    assert 'private-prompt' not in output
    assert 'private-result' not in output


def test_unhandled_errors_are_logged_without_dumping_update(log_dir):
    path = configure_logging()
    try:
        raise RuntimeError('scheduled-job-failure')
    except RuntimeError as error:
        update = SimpleNamespace(update_id=42, effective_chat=SimpleNamespace(id=123),
                                 text='private-user-message')
        context = SimpleNamespace(error=error, job=SimpleNamespace(name='ping_users'))
        asyncio.run(main.handle_application_error(update, context))
        main.handle_polling_error(error)
        main.handle_async_error(None, {'message': 'task failed', 'exception': error})

    output = path.read_text()
    assert 'Unhandled Telegram error update_id=42 chat_id=123 job=ping_users' in output
    assert 'Telegram polling failed' in output
    assert 'Unhandled background task error' in output
    assert 'Traceback' in output
    assert 'private-user-message' not in output


def test_caught_request_failure_logs_chat_and_traceback(log_dir, monkeypatch, running_activities):
    path = configure_logging()
    sent = []

    def broken_user_config(chat_id):
        raise RuntimeError('failed-to-read-config')

    async def send_message(bot, chat_id, text, **kwargs):
        sent.append(text)

    monkeypatch.setattr(main, 'busy_users', set())
    monkeypatch.setattr(main, 'running_activities', running_activities, raising=False)
    monkeypatch.setattr(main, 'user_config', SimpleNamespace(get_user_data=broken_user_config), raising=False)
    monkeypatch.setattr(main, 'lang_map', {'test-token': 'english'}, raising=False)
    monkeypatch.setattr(main, 'interface', {
        'Something went terribly wrong, please try again or notify the admin': {'english': 'error reply'},
    }, raising=False)
    monkeypatch.setattr(main, 'tel_send_message', send_message)
    update = SimpleNamespace(message=SimpleNamespace(chat_id=123, text='private-message'))
    context = SimpleNamespace(_application=SimpleNamespace(bot=SimpleNamespace(token='test-token')))

    asyncio.run(main.handle_request(update, context))

    output = path.read_text()
    assert 'Message processing failed chat_id=123' in output
    assert 'Traceback' in output
    assert 'failed-to-read-config' in output
    assert 'private-message' not in output
    assert sent == ['error reply']
    assert not main.busy_users


@pytest.mark.parametrize('mode', ['failure', 'running', 'signal_during_startup'])
def test_polling_errors_and_shutdown_are_logged_and_cleaned_up(log_dir, monkeypatch, mode):
    path = configure_logging()
    signals = {}
    events = []
    monkeypatch.setattr(main.signal, 'signal', lambda signum, handler: signals.__setitem__(signum, handler))
    monkeypatch.setattr(main, 'running_activities', SimpleNamespace(
        backup=lambda: events.append('backup')), raising=False)

    async def start_polling(**kwargs):
        assert kwargs['error_callback'] is main.handle_polling_error
        if mode == 'failure':
            raise RuntimeError('polling-startup-failure')
        if mode == 'running':
            updater.running = True
            asyncio.get_running_loop().call_soon(signals[signal.SIGTERM], signal.SIGTERM, None)
        else:
            signals[signal.SIGTERM](signal.SIGTERM, None)
            try:
                await asyncio.Event().wait()
            finally:
                events.append('polling-cancelled')

    updater = SimpleNamespace(running=False, start_polling=start_polling, stop=AsyncMock())
    application = SimpleNamespace(initialize=AsyncMock(), start=AsyncMock(),
                                  stop=AsyncMock(), shutdown=AsyncMock(), updater=updater)

    async def run():
        await asyncio.wait_for(main.run_apps([application]), timeout=1)

    if mode == 'failure':
        with pytest.raises(RuntimeError, match='polling-startup-failure'):
            asyncio.run(run())
    else:
        asyncio.run(run())

    application.stop.assert_awaited_once()
    application.shutdown.assert_awaited_once()
    assert events[-1] == 'backup'
    output = path.read_text()
    assert 'Bot stopping' in output
    assert 'Bot stopped' in output
    if mode == 'failure':
        assert 'Telegram polling startup failed' in output
        assert 'Traceback' in output
    else:
        assert 'Shutdown requested signal=SIGTERM' in output
    if mode == 'running':
        updater.stop.assert_awaited_once()
    if mode == 'signal_during_startup':
        assert 'polling-cancelled' in events


class OfflineBot(ExtBot):
    """Run the real Telegram dispatcher without HTTP calls."""

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    @property
    def username(self):
        return 'flashbot_test'


def interaction_update(bot, kind, *, username='alice', action='Next', chat_id=123):
    user = {'id': 321, 'is_bot': False, 'first_name': 'Private name'}
    if username is not None:
        user['username'] = username
    message = {'message_id': 10, 'date': 1793440800,
               'chat': {'id': chat_id, 'type': 'private'}, 'from': user}
    if kind == 'button':
        message['text'] = 'private-exercise-prompt'
        payload = {'callback_query': {'id': 'callback-1', 'from': user,
                                     'chat_instance': 'chat-1', 'message': message, 'data': action}}
    else:
        if kind == 'chat_message':
            message['text'] = 'private-chat-response'
        elif kind == 'photo':
            message['photo'] = [{'file_id': 'photo-1', 'file_unique_id': 'unique-1',
                                 'width': 100, 'height': 100}]
            message['caption'] = 'private-photo-caption'
        else:
            command = '/next_test' if kind == 'command' else '/unknown'
            message['text'] = command + ' private-command-arguments'
            message['entities'] = [{'type': 'bot_command', 'offset': 0, 'length': len(command)}]
        payload = {'message': message}
    return Update.de_json({'update_id': 777, **payload}, bot)


@pytest.mark.parametrize('kind', ['command', 'button', 'chat_message', 'unknown_command', 'photo'])
def test_dispatcher_logs_all_interactions_before_busy_checks(log_dir, monkeypatch, kind):
    path = configure_logging()
    application = Application.builder().bot(OfflineBot('123456:test-token')).updater(None).job_queue(None).build()
    main.register_handlers(application)
    # Business handlers must reject these requests without losing the audit event.
    monkeypatch.setattr(main, 'busy_users', {123, 321})
    monkeypatch.setattr(main, 'lang_map', {application.bot.token: 'english'}, raising=False)
    monkeypatch.setattr(main, 'user_config', SimpleNamespace(
        is_user_allowed=lambda user_id: user_id == 321), raising=False)
    notifications = []

    async def busy_notification(update, context, chat_id):
        assert 'User interaction' in path.read_text(), 'interaction must be logged before dispatch'
        notifications.append(chat_id)

    monkeypatch.setattr(main, 'notify_user_is_busy', busy_notification)
    update = interaction_update(application.bot, kind, chat_id=321)

    async def run():
        async with application:
            await application.process_update(update)

    asyncio.run(run())

    interactions = [row for row in read_rows(path) if row['event'] == 'user_interaction']
    assert len(interactions) == 1
    entry = interactions[0]
    assert entry['user_id'] == '321'
    assert entry['username'] == 'alice'
    assert entry['chat_id'] == '321'
    assert entry['update_id'] == '777'
    assert entry['timestamp'].endswith('Z')
    assert entry['level'] == 'INFO'
    assert entry['logger'] == 'main'
    assert entry['message'] == 'User interaction'
    if kind == 'command':
        assert entry['kind'] == 'command' and entry['action'] == '/next_test'
    elif kind == 'unknown_command':
        assert entry['action'] == '/unknown'
    elif kind == 'button':
        assert entry['kind'] == 'button' and entry['action'] == 'Next'
    else:
        assert entry['kind'] == 'chat_message'
        assert entry['message_type'] == ('photo' if kind == 'photo' else 'text')
    assert notifications == ([321] if kind in {'button', 'command', 'chat_message'} else [])
    assert 'private-' not in path.read_text()
    assert 'Private name' not in path.read_text()



def test_expired_button_is_logged_with_sender_even_without_username(log_dir, monkeypatch, running_activities):
    path = configure_logging()
    application = Application.builder().bot(OfflineBot('123456:test-token')).updater(None).job_queue(None).build()
    main.register_handlers(application)
    monkeypatch.setattr(main, 'busy_users', set())
    monkeypatch.setattr(main, 'lang_map', {application.bot.token: 'english'}, raising=False)
    monkeypatch.setattr(main, 'user_config', SimpleNamespace(
        is_user_allowed=lambda user_id: user_id == 321,
        get_user_data=lambda chat_id: {'language': 'english'}), raising=False)
    monkeypatch.setattr(main, 'running_activities', running_activities, raising=False)
    monkeypatch.setattr(main, 'exercise_buttons', ['Next'], raising=False)
    monkeypatch.setattr(main, 'interface', {
        'Sorry, the exercise has been completed or is expired': {'english': 'expired'},
    }, raising=False)
    send = AsyncMock()
    monkeypatch.setattr(main, 'tel_send_message', send)

    async def run():
        async with application:
            await application.process_update(interaction_update(application.bot, 'button', username=None, chat_id=321))

    asyncio.run(run())

    output = path.read_text()
    entry = next(row for row in read_rows(path) if row['event'] == 'user_interaction')
    assert entry['kind'] == 'button'
    assert entry['user_id'] == '321'
    assert entry['username'] == ''
    assert entry['chat_id'] == '321'
    assert entry['action'] == 'Next'
    assert 'Button ignored reason=exercise_expired chat_id=321' in output
    send.assert_awaited_once_with(application.bot, 321, 'expired')
