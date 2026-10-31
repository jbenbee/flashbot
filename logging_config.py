"""Bounded, credential-redacted logging for the bot (standard library only)."""

import asyncio
import csv
from functools import wraps
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import time


USER_INTERACTION_EVENT = 'user_interaction'
CSV_FIELDS = ('timestamp', 'level', 'logger', 'event', 'kind', 'user_id', 'username',
              'chat_id', 'update_id', 'action', 'message_type', 'message', 'exception')


def _csv_row(values):
    output = io.StringIO(newline='')
    csv.writer(output, lineterminator='\n').writerow(values)
    return output.getvalue().removesuffix('\n')


CSV_HEADER = _csv_row(CSV_FIELDS) + '\n'


def _truncate(text, max_bytes):
    encoded = text.encode('utf-8', errors='replace')
    if len(encoded) <= max_bytes:
        return encoded.decode('utf-8')
    suffix = b' ...[truncated]'[:max_bytes]
    return encoded[:max_bytes - len(suffix)].decode('utf-8', errors='ignore') + suffix.decode()


class UserInteractionFilter(logging.Filter):
    def filter(self, record):
        return getattr(record, 'event', None) == USER_INTERACTION_EVENT


class SafeFormatter(logging.Formatter):
    """Redact after formatting so exception messages and tracebacks are covered."""

    converter = time.gmtime

    def __init__(self, secrets=(), max_bytes=16 * 1024):
        super().__init__('%(asctime)sZ %(levelname)s %(name)s event=%(event)s %(message)s',
                         datefmt='%Y-%m-%dT%H:%M:%S')
        self.secrets = sorted({value for value in secrets if value}, key=len, reverse=True)
        self.max_bytes = max_bytes

    def format(self, record):
        if not hasattr(record, 'event'):
            record.event = 'application'
        message = super().format(record)
        if record.event == USER_INTERACTION_EVENT and hasattr(record, 'kind'):
            message += ' ' + ' '.join(
                f'{field}={json.dumps(getattr(record, field, None), ensure_ascii=False) if field == "action" else getattr(record, field, None)}'
                for field in ('kind', 'user_id', 'username', 'chat_id', 'update_id', 'action', 'message_type'))
        return _truncate(self.clean(message), self.max_bytes)

    def clean(self, value):
        message = '' if value is None else str(value)
        for secret in self.secrets:
            message = message.replace(secret, '[REDACTED]')
        message = re.sub(r'\b(?:bot)?\d{5,}:[A-Za-z0-9_-]{20,}', '[REDACTED]', message)
        message = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', message)
        message = re.sub(r'(?i)(\bBearer\s+)[^\s\'"<>]+', r'\1[REDACTED]', message)
        message = re.sub(r'(?i)([?&](?:api[_-]?key|token|key|access_token)=)[^\s&\'"<>]+',
                         r'\1[REDACTED]', message)
        # Keep each event on one physical line, even when it contains a traceback.
        message = message.replace('\r', r'\r').replace('\n', r'\n')
        return message.encode('utf-8', errors='replace').decode('utf-8')


class CsvFormatter(SafeFormatter):
    """Serialize complete CSV records; truncate cells before quoting, never rows."""

    def format(self, record):
        exception = self.formatException(record.exc_info) if record.exc_info else (record.exc_text or '')
        if record.stack_info:
            exception += '\n' + record.stack_info
        values = {
            'timestamp': self.formatTime(record, self.datefmt) + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'event': getattr(record, 'event', 'application'),
            'message': record.getMessage(),
            'exception': exception,
        }
        for field in ('kind', 'user_id', 'username', 'chat_id', 'update_id', 'action', 'message_type'):
            values[field] = getattr(record, field, None)
        cells = [self.clean(values[field]) for field in CSV_FIELDS]
        row = _csv_row(cells)
        if len(row.encode('utf-8')) <= self.max_bytes:
            return row
        # Find a per-cell byte limit that fits after CSV escaping (quotes double).
        low, high = 0, max(len(cell.encode('utf-8')) for cell in cells)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = _csv_row([_truncate(cell, middle) for cell in cells])
            if len(candidate.encode('utf-8')) <= self.max_bytes:
                low = middle
            else:
                high = middle - 1
        return _csv_row([_truncate(cell, low) for cell in cells])


class BoundedRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        stream = open(self.baseFilename, self.mode, encoding=self.encoding,
                      errors=self.errors, newline='',
                      opener=lambda path, flags: os.open(path, flags, 0o600))
        if stream.tell() == 0:
            stream.write(CSV_HEADER)
            stream.flush()
        return stream

    def shouldRollover(self, record):
        # The standard handler counts characters; UTF-8 events can use more bytes.
        if self.stream is None:
            self.stream = self._open()
        size = len((self.format(record) + self.terminator).encode('utf-8'))
        return self.stream.tell() + size >= self.maxBytes


def _csv_backup_name(default_name):
    # RotatingFileHandler supplies "flashbot.csv.1"; keep a .csv extension.
    path = Path(default_name)
    number = path.suffix
    return str(path.with_suffix('').with_suffix(number + '.csv'))


def configure_logging():
    """Configure once at startup; repeated calls replace only our own handlers.

    One process must own each log directory. Rotation is thread-safe, but Python's
    RotatingFileHandler does not coordinate multiple processes writing one file.
    """
    level_name = os.getenv('FLASHBOT_LOG_LEVEL', 'INFO').upper()
    if level_name not in {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}:
        raise ValueError('FLASHBOT_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL')
    max_bytes = int(os.getenv('FLASHBOT_LOG_MAX_BYTES', str(5 * 1024 * 1024)))
    backups = int(os.getenv('FLASHBOT_LOG_BACKUP_COUNT', '3'))
    if max_bytes < 1024 or backups < 1:
        raise ValueError('Log max bytes must be >= 1024 and backup count must be >= 1')
    console = os.getenv('FLASHBOT_LOG_CONSOLE', '0')
    if console not in {'0', '1'}:
        raise ValueError('FLASHBOT_LOG_CONSOLE must be 0 or 1')
    console_user_only = os.getenv('FLASHBOT_LOG_CONSOLE_USER_ONLY', '0')
    if console_user_only not in {'0', '1'}:
        raise ValueError('FLASHBOT_LOG_CONSOLE_USER_ONLY must be 0 or 1')

    log_dir = Path(os.getenv('FLASHBOT_LOG_DIR', str(Path(__file__).resolve().parent / 'logs'))).expanduser().resolve()
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = log_dir / 'flashbot.csv'
    handler = BoundedRotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backups,
                                         encoding='utf-8')
    handler.namer = _csv_backup_name
    log_path.chmod(0o600)
    secrets = [value for key, value in os.environ.items()
               if any(part in key.upper() for part in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD'))]
    row_budget = min(16 * 1024 - 1, max_bytes - len(CSV_HEADER.encode('utf-8')) - 1)
    csv_formatter = CsvFormatter(secrets, max_bytes=row_budget)
    handler.setFormatter(csv_formatter)
    handlers = [handler]
    if console == '1':
        console_handler = logging.StreamHandler()
        if console_user_only == '1':
            console_handler.addFilter(UserInteractionFilter())
        console_handler.setFormatter(SafeFormatter(secrets))
        handlers.append(console_handler)
    root = logging.getLogger()
    for previous in root.handlers[:]:
        if getattr(previous, '_flashbot_handler', False):
            root.removeHandler(previous)
            previous.close()
    root.setLevel(level_name)
    for output in handlers:
        output._flashbot_handler = True
        output.setLevel(level_name)
        root.addHandler(output)
    # These libraries can emit URLs containing bot tokens or full API payloads at DEBUG.
    for name in ('httpx', 'httpcore', 'openai', 'telegram', 'apscheduler', 'websockets'):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.captureWarnings(True)
    logging.getLogger(__name__).info(
        'Logging configured path=%s level=%s max_bytes=%s backups=%s console=%s console_user_only=%s',
        log_path, level_name, max_bytes, backups, console, console_user_only)
    return log_path


def logged_operation(func):
    """Log async external operations without recording their arguments or results."""
    logger = logging.getLogger(func.__module__)

    @wraps(func)
    async def wrapper(*args, **kwargs):
        started = time.monotonic()
        logger.info('Operation started operation=%s', func.__name__)
        try:
            result = await func(*args, **kwargs)
        except asyncio.CancelledError:
            logger.info('Operation cancelled operation=%s duration_seconds=%.3f',
                        func.__name__, time.monotonic() - started)
            raise
        except Exception:
            logger.exception('Operation failed operation=%s duration_seconds=%.3f',
                             func.__name__, time.monotonic() - started)
            raise
        logger.info('Operation completed operation=%s duration_seconds=%.3f',
                    func.__name__, time.monotonic() - started)
        return result

    return wrapper
