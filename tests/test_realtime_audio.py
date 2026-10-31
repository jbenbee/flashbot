import asyncio
import base64
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import wave

import openai
import pytest
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

import openai.lib._websocket as websocket_transport
import utils
from model_config import load_model_config


@pytest.fixture
def mock_realtime(monkeypatch):
    """Use real SDK serialization and event parsing with a mocked WebSocket."""
    monkeypatch.setenv('OPENAI_KEY', 'test-key')
    clients = []
    sockets = []
    client_cls = openai.AsyncOpenAI

    def install(events, *, stall=False):
        state = SimpleNamespace(sent=[], urls=[], closed=False, receiving=asyncio.Event())
        pending = iter(events)

        async def send(data):
            state.sent.append(json.loads(data))

        async def recv(**kwargs):
            state.receiving.set()
            event = next(pending, None)
            if event is not None:
                return json.dumps(event).encode()
            if stall:
                await asyncio.Event().wait()
            raise ConnectionClosedOK(Close(1000, ''), Close(1000, ''), True)

        async def close(**kwargs):
            state.closed = True

        async def connect(url, **kwargs):
            state.urls.append(url)
            return SimpleNamespace(send=send, recv=recv, close=close)

        def make_client(**kwargs):
            client = client_cls(**kwargs)
            clients.append(client)
            return client

        sockets.append(state)
        monkeypatch.setattr(websocket_transport, '_WebSocketConnect', connect)
        monkeypatch.setattr(utils.openai, 'AsyncOpenAI', make_client)
        return state

    yield install
    assert clients and all(client.is_closed() for client in clients)
    assert sockets and all(socket.closed for socket in sockets)


def audio_delta(data):
    return {
        'type': 'response.output_audio.delta', 'event_id': 'evt_audio',
        'response_id': 'resp_test', 'item_id': 'item_test',
        'output_index': 0, 'content_index': 0,
        'delta': base64.b64encode(data).decode(),
    }


def response_done(status='completed'):
    return {
        'type': 'response.done', 'event_id': 'evt_done',
        'response': {'id': 'resp_test', 'status': status, 'output': []},
    }


def test_realtime_audio_chunks_are_saved_as_wav(mock_realtime, tmp_path):
    pcm_data = b'\x01\x00\x02\x00'
    state = mock_realtime([
        audio_delta(pcm_data[:3]),
        {'type': 'response.output_audio_transcript.delta', 'delta': 'ciao'},
        audio_delta(pcm_data[3:]),
        {'type': 'response.output_audio.done'},
        response_done(),
    ])
    destination = tmp_path / 'pronunciation.wav'

    asyncio.run(utils.get_audio('ciao', 'Italian', destination))

    with wave.open(str(destination), 'rb') as audio_file:
        assert audio_file.getnchannels() == 1
        assert audio_file.getsampwidth() == 2
        assert audio_file.getframerate() == 24000
        assert audio_file.readframes(audio_file.getnframes()) == pcm_data
    url = urlparse(state.urls[0])
    assert url.scheme == 'wss'
    assert url.path == '/v1/realtime'
    assert parse_qs(url.query) == {'model': ['gpt-realtime-2.1-mini']}
    session, item, response = state.sent
    assert session['type'] == 'session.update'
    config = session['session']
    assert config['type'] == 'realtime'
    assert config['output_modalities'] == ['audio']
    assert config['audio'] == {
        'input': {'turn_detection': None},
        'output': {'voice': 'alloy', 'format': {'type': 'audio/pcm', 'rate': 24000}},
    }
    assert 'Italian' in config['instructions']
    assert 'exactly as written' in config['instructions']
    assert item == {
        'type': 'conversation.item.create',
        'item': {'type': 'message', 'role': 'user',
                 'content': [{'type': 'input_text', 'text': 'ciao'}]},
    }
    assert response == {'type': 'response.create'}


def test_speech_model_and_session_are_read_from_config(mock_realtime, tmp_path, monkeypatch):
    config = load_model_config().model_copy(deep=True)
    config.speech.model = 'configured-speech'
    config.speech.session['audio']['output']['voice'] = 'coral'
    state = mock_realtime([audio_delta(b'\x01\x00'), response_done()])
    monkeypatch.setattr(utils, 'load_model_config', lambda: config)

    asyncio.run(utils.get_audio('ciao', 'Italian', tmp_path / 'pronunciation.wav'))

    assert parse_qs(urlparse(state.urls[0]).query) == {'model': ['configured-speech']}
    session = state.sent[0]['session']
    assert session['audio']['output']['voice'] == 'coral'
    assert 'Italian' in session['instructions']
    assert '{lang}' in config.speech.session['instructions']


@pytest.mark.parametrize('status', ['failed', 'incomplete', 'cancelled'])
def test_unsuccessful_response_preserves_existing_file(mock_realtime, tmp_path, status):
    mock_realtime([audio_delta(b'\x00\x00'), response_done(status)])
    destination = tmp_path / 'pronunciation.wav'
    destination.write_bytes(b'existing audio')

    with pytest.raises(ValueError, match=f'Speech generation ended: {status}'):
        asyncio.run(utils.get_audio('ciao', 'Italian', destination))

    assert destination.read_bytes() == b'existing audio'


@pytest.mark.parametrize('events, error_type, message', [
    ([{'type': 'error', 'event_id': 'evt_error',
       'error': {'type': 'invalid_request_error', 'message': 'Invalid voice'}}],
     openai.OpenAIError, 'Invalid voice'),
    ([audio_delta(b'\x00\x00')], ValueError, 'connection closed'),
    ([response_done()], ValueError, 'empty or invalid PCM'),
    ([audio_delta(b'\x00'), response_done()], ValueError, 'empty or invalid PCM'),
    ([{**audio_delta(b''), 'delta': '!!!'}], ValueError, 'base64'),
])
def test_audio_errors_do_not_write_files(mock_realtime, tmp_path, events, error_type, message):
    mock_realtime(events)
    destination = tmp_path / 'pronunciation.wav'

    with pytest.raises(error_type, match=message):
        asyncio.run(utils.get_audio('ciao', 'Italian', destination))

    assert not destination.exists()


def test_stalled_stream_times_out_and_closes_resources(mock_realtime, tmp_path, monkeypatch):
    mock_realtime([audio_delta(b'\x00\x00')], stall=True)
    destination = tmp_path / 'pronunciation.wav'
    real_timeout = asyncio.timeout
    deadlines = []

    def short_timeout(delay):
        deadlines.append(delay)
        return real_timeout(0.01)

    monkeypatch.setattr(utils.asyncio, 'timeout', short_timeout)

    with pytest.raises(TimeoutError):
        asyncio.run(utils.get_audio('ciao', 'Italian', destination))

    assert deadlines == [20.0]
    assert not destination.exists()


def test_cancelled_request_closes_resources(mock_realtime, tmp_path):
    state = mock_realtime([], stall=True)
    destination = tmp_path / 'pronunciation.wav'

    async def run():
        request = asyncio.create_task(utils.get_audio('ciao', 'Italian', destination))
        await asyncio.wait_for(state.receiving.wait(), timeout=1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

    asyncio.run(run())

    assert not destination.exists()
