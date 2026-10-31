import asyncio
from types import SimpleNamespace

import openai
import pytest

import utils


def test_third_attempt_success_uses_substitute_model(monkeypatch):
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
        'Question', 'english', 'primary', 'substitute'))

    assert result == 'answer'
    assert models == ['primary', 'primary', 'substitute']


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
            'Question', 'english', 'primary', 'substitute'))

    assert models == ['primary', 'primary', 'substitute']
