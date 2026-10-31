import asyncio
import json

import httpx
import pytest
import yaml

from model_config import MODEL_CONFIG_PATH, load_model_config
import utils


def test_default_config_is_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_model_config.cache_clear()

    config = load_model_config()

    assert config.assistant.model_base.model
    assert config.assistant.model_fallback.model
    assert config.speech.model == 'gpt-realtime-2.1-mini'
    assert config.song.model == 'V6'


@pytest.mark.parametrize(('section', 'key', 'value'), [
    ('assistant', 'model_fallback', None),
    ('client', 'timeout_seconds', 0),
    ('assistant', 'max_attempts', 0),
    ('speech', 'timeout_seconds', -1),
    ('song', 'max_poll_attempts', 0),
    ('assistant', 'unexpected_model', 'typo'),
])
def test_invalid_model_settings_fail_with_config_path(tmp_path, section, key, value):
    data = yaml.safe_load(MODEL_CONFIG_PATH.read_text())
    data[section][key] = value
    config_path = tmp_path / 'models.yaml'
    config_path.write_text(yaml.safe_dump(data))

    with pytest.raises(ValueError, match='Invalid model configuration') as error:
        load_model_config(config_path)

    assert str(config_path) in str(error.value)


@pytest.mark.parametrize('params', [{'stream': True}, {'background': True}, {'text': None}])
def test_incompatible_response_params_are_rejected(tmp_path, params):
    data = yaml.safe_load(MODEL_CONFIG_PATH.read_text())
    data['assistant']['model_base']['params'] = params
    config_path = tmp_path / 'models.yaml'
    config_path.write_text(yaml.safe_dump(data))

    with pytest.raises(ValueError, match='Invalid model configuration'):
        load_model_config(config_path)


def test_non_pcm_audio_is_rejected_before_requesting_audio(tmp_path):
    data = yaml.safe_load(MODEL_CONFIG_PATH.read_text())
    data['speech']['session']['audio']['output']['format'] = {'type': 'audio/pcmu'}
    config_path = tmp_path / 'models.yaml'
    config_path.write_text(yaml.safe_dump(data))

    with pytest.raises(ValueError, match='audio/pcm at 24000 Hz'):
        load_model_config(config_path)


@pytest.mark.parametrize('contents', ['', 'assistant: [invalid'])
def test_empty_or_malformed_yaml_is_rejected(tmp_path, contents):
    config_path = tmp_path / 'models.yaml'
    config_path.write_text(contents)

    with pytest.raises(ValueError, match='Invalid model configuration'):
        load_model_config(config_path)


def test_song_request_uses_configured_model_and_params(tmp_path, monkeypatch):
    config = load_model_config().model_copy(deep=True)
    config.song.model = 'configured-song'
    config.song.params['instrumental'] = True
    config.song.timeout_seconds = 12.0
    requests = []
    timeouts = []
    client_cls = httpx.AsyncClient

    def handle(request):
        if request.method == 'POST':
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'data': {'taskId': 'task_test'}})
        return httpx.Response(200, json={'data': {
            'status': 'SUCCESS',
            'response': {'sunoData': [{
                'audioUrl': 'https://example.test/song.mp3',
                'imageUrl': 'https://example.test/image.jpg',
                'prompt': 'lyrics', 'title': 'Song',
            }]},
        }})

    def make_client(**kwargs):
        timeouts.append(kwargs['timeout'])
        return client_cls(transport=httpx.MockTransport(handle), **kwargs)

    async def no_download(*args):
        pass

    monkeypatch.setattr(utils, 'load_model_config', lambda: config)
    monkeypatch.setattr(utils.httpx, 'AsyncClient', make_client)
    monkeypatch.setattr(utils, 'download_file', no_download)

    result = asyncio.run(utils.generate_song_of_the_day('test prompt', tmp_path))

    assert result['title'] == 'Song'
    assert result['audio_data'][0]['lyrics'] == 'lyrics'
    assert result['audio_data'][0]['title'] == 'Song'
    assert requests == [{
        'model': 'configured-song', 'prompt': 'test prompt',
        'customMode': False, 'instrumental': True, 'callBackUrl': 'test.url',
    }]
    assert timeouts == [12.0]


@pytest.mark.parametrize('track_count', [1, 2])
@pytest.mark.parametrize(('audio_field', 'image_field'), [
    ('audioUrl', 'imageUrl'), ('audio_url', 'image_url'),
])
@pytest.mark.parametrize('style', [None, 'jazz'])
def test_v6_request_and_per_track_metadata(tmp_path, monkeypatch, track_count, audio_field, image_field, style):
    config = load_model_config().model_copy(deep=True)
    config.song.model = 'V6'
    requests = []
    client_cls = httpx.AsyncClient
    tracks = [{
        audio_field: f'https://example.test/song{index}.mp3',
        image_field: 'https://example.test/image.jpg',
        'prompt': f'Lyrics for track {index}',
        'title': f'Song {index}',
    } for index in range(track_count)]

    def handle(request):
        if request.method == 'POST':
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'data': {'taskId': 'task_test'}})
        if request.url.path.endswith('record-info'):
            return httpx.Response(200, json={'data': {
                'status': 'SUCCESS', 'response': {'sunoData': tracks},
            }})
        return httpx.Response(200, content=b'downloaded')

    monkeypatch.setattr(utils, 'load_model_config', lambda: config)
    monkeypatch.setattr(utils.httpx, 'AsyncClient', lambda **kwargs: client_cls(
        transport=httpx.MockTransport(handle), **kwargs))

    result = asyncio.run(utils.generate_song_of_the_day('Song idea', tmp_path, style=style))

    assert requests[0]['model'] == 'V6'
    assert requests[0]['customMode'] is False
    assert requests[0]['style'] == (style or 'Song idea')
    assert requests[0]['prompt'] == 'Song idea'
    assert len(result['audio_data']) == track_count
    for index, track in enumerate(result['audio_data']):
        assert track['title'] == f'Song {index}'
        assert track['lyrics'] == f'Lyrics for track {index}'
        assert track['audio_file_path'].read_bytes() == b'downloaded'
    assert result['img_path'].read_bytes() == b'downloaded'
