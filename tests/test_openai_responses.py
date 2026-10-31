import asyncio
import copy
import json

import httpx2 as httpx
import openai
import pytest
from pydantic import BaseModel, ValidationError

import utils


@pytest.fixture
def mock_openai(monkeypatch):
    """Exercise the real SDK's request and response handling without API calls."""
    monkeypatch.setenv('OPENAI_KEY', 'test-key')
    requests = []
    clients = []
    client_cls = openai.AsyncOpenAI

    def install(response_body, *, status_code=200):
        def handle(request):
            requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(status_code, json=response_body)

        def make_client(**kwargs):
            kwargs['api_key'] = 'test-key'
            http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            clients.append(http_client)
            return client_cls(http_client=http_client, **kwargs)

        monkeypatch.setattr(utils.openai, 'AsyncOpenAI', make_client)
        return requests

    yield install
    assert clients and all(client.is_closed for client in clients)


def text_response(text='answer', *, status='completed'):
    return {
        'id': 'resp_test', 'object': 'response', 'created_at': 0,
        'model': 'test-model', 'status': status,
        'output': [{
            'id': 'msg_test', 'type': 'message', 'role': 'assistant',
            'status': 'completed',
            'content': [{'type': 'output_text', 'text': text, 'annotations': []}],
        }],
    }


def assistant_response(**kwargs):
    return asyncio.run(utils.get_assistant_response(
        {'You are a great language teacher': {'english': 'Teach'}},
        'Question', 'english', 'primary', 'substitute', **kwargs))


def test_plain_text_request_uses_responses(mock_openai):
    response = text_response()
    response['output'].insert(0, {'id': 'rs_test', 'type': 'reasoning', 'summary': []})
    requests = mock_openai(response)

    assert assistant_response() == 'answer'
    assert requests == [('/v1/responses', {
        'model': 'primary', 'instructions': 'Teach', 'input': 'Question',
        'text': {}, 'store': False,
    })]


class Answer(BaseModel):
    answer: str


def test_json_schema_is_flattened_and_validated(mock_openai):
    response_format = {
        'type': 'json_schema',
        'json_schema': {'name': 'answer', 'strict': True, 'schema': Answer.model_json_schema()},
    }
    original_format = copy.deepcopy(response_format)
    requests = mock_openai(text_response('{"answer":"hello"}'))

    result = assistant_response(response_format=response_format, validation_cls=Answer)

    assert result == Answer(answer='hello')
    assert requests[0][1]['text'] == {
        'format': {'type': 'json_schema', **response_format['json_schema']},
    }
    assert response_format == original_format


def test_json_object_format_is_preserved(mock_openai):
    requests = mock_openai(text_response('{"answer":"hello"}'))

    assert assistant_response(response_format={'type': 'json_object'}) == '{"answer":"hello"}'
    assert requests[0][1]['text'] == {'format': {'type': 'json_object'}}


def test_invalid_structured_output_is_rejected(mock_openai):
    mock_openai(text_response('{"unexpected":"value"}'))

    with pytest.raises(ValidationError):
        assistant_response(validation_cls=Answer)


def test_refusal_is_reported_before_validation(mock_openai):
    response = text_response()
    response['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'Cannot comply'}]
    mock_openai(response)

    with pytest.raises(ValueError, match='refused to respond'):
        assistant_response(validation_cls=Answer)


@pytest.mark.parametrize(('status', 'text'), [
    ('incomplete', 'partial answer'), ('failed', ''), ('completed', ''),
])
def test_unusable_output_is_rejected(mock_openai, status, text):
    mock_openai(text_response(text, status=status))

    with pytest.raises(ValueError, match='could not respond'):
        assistant_response()
