import asyncio
from types import SimpleNamespace

import openai
import pytest

import utils
from model_config import load_model_config


def test_third_attempt_success_uses_fallback_model(monkeypatch):
    models = []

    async def create(*, model, **kwargs):
        models.append(model)
        if len(models) < 3:
            raise openai.OpenAIError('transient failure')
        return SimpleNamespace(status='completed', output=[], output_text='answer')

    async def no_sleep(_):
        pass

    async def close():
        pass

    monkeypatch.setattr(utils.openai, 'AsyncOpenAI', lambda **kwargs: SimpleNamespace(
        responses=SimpleNamespace(create=create), close=close))
    monkeypatch.setattr(utils.asyncio, 'sleep', no_sleep)

    result = asyncio.run(utils.get_assistant_response(
        {'You are a great language teacher': {'english': 'Teach'}},
        'Question', 'english', model_base='primary', model_fallback='fallback'))

    assert result == 'answer'
    assert models == ['primary', 'primary', 'fallback']


def test_configured_models_use_their_own_params_on_fallback(monkeypatch):
    config = load_model_config().model_copy(deep=True)
    config.assistant.model_base.model = 'configured-primary'
    config.assistant.model_base.params = {'store': False, 'max_output_tokens': 400,
                                          'text': {'verbosity': 'low'}}
    config.assistant.model_fallback.model = 'configured-fallback'
    config.assistant.model_fallback.params = {'store': False, 'max_output_tokens': 800}
    config.assistant.retry_delay_seconds = 0
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        if len(requests) < 3:
            raise openai.OpenAIError('transient failure')
        return SimpleNamespace(status='completed', output=[], output_text='answer')

    async def close():
        pass

    monkeypatch.setattr(utils, 'load_model_config', lambda: config)
    monkeypatch.setattr(utils.openai, 'AsyncOpenAI', lambda **kwargs: SimpleNamespace(
        responses=SimpleNamespace(create=create), close=close))

    result = asyncio.run(utils.get_assistant_response(
        {'You are a great language teacher': {'english': 'Teach'}},
        'Question', 'english', response_format={'type': 'json_object'}))

    assert result == 'answer'
    assert [request['model'] for request in requests] == [
        'configured-primary', 'configured-primary', 'configured-fallback',
    ]
    assert [request['max_output_tokens'] for request in requests] == [400, 400, 800]
    assert requests[0]['text'] == {'verbosity': 'low', 'format': {'type': 'json_object'}}
    assert requests[2]['text'] == {'format': {'type': 'json_object'}}
    assert config.assistant.model_base.params['text'] == {'verbosity': 'low'}


def test_three_failed_attempts_raise_value_error(monkeypatch):
    models = []

    async def create(*, model, **kwargs):
        models.append(model)
        raise openai.OpenAIError('transient failure')

    async def no_sleep(_):
        pass

    async def close():
        pass

    monkeypatch.setattr(utils.openai, 'AsyncOpenAI', lambda **kwargs: SimpleNamespace(
        responses=SimpleNamespace(create=create), close=close))
    monkeypatch.setattr(utils.asyncio, 'sleep', no_sleep)

    with pytest.raises(ValueError, match='could not respond'):
        asyncio.run(utils.get_assistant_response(
            {'You are a great language teacher': {'english': 'Teach'}},
            'Question', 'english', model_base='primary', model_fallback='fallback'))

    assert models == ['primary', 'primary', 'fallback']
