import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

import utils


@pytest.fixture
def weather_service(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz)

    fixed_now = datetime.fromisoformat('2026-11-01T12:00:00+00:00')
    monkeypatch.setattr(utils, 'datetime', FrozenDateTime)
    client_cls = httpx.AsyncClient
    requests = []

    def configure(data, *, status=200, now=None):
        nonlocal fixed_now
        if now:
            fixed_now = datetime.fromisoformat(now)

        def handle(request):
            requests.append(request)
            return httpx.Response(status, json=data)

        monkeypatch.setattr(utils.httpx, 'AsyncClient', lambda **kwargs: client_cls(
            transport=httpx.MockTransport(handle), **kwargs))

    return configure, requests


@pytest.mark.parametrize('include_temperatures', [False, True])
def test_weather_returns_only_a_qualitative_mood_cue(weather_service, include_temperatures):
    data = {'current_condition': [{
        'weatherDesc': [{'value': 'Light rain '}],
        'localObsDateTime': '2026-11-01 12:30 PM',
    }]}
    if include_temperatures:
        data['current_condition'][0]['temp_C'] = '14'
        data['weather'] = [{'mintempC': '10', 'maxtempC': '18'}]
    configure, requests = weather_service
    configure(data)

    weather = asyncio.run(utils.get_weather_forecast(ZoneInfo('Europe/Rome'), location='Bolzano, Italy'))

    assert weather == 'Light rain'
    assert requests[0].url.path == '/Bolzano, Italy'
    assert dict(requests[0].url.params) == {'format': 'j1', 'lang': 'en'}


@pytest.mark.parametrize('location', [None, '', '  '])
def test_missing_location_skips_weather_without_guessing_from_timezone(weather_service, location):
    configure, requests = weather_service
    configure({})

    assert asyncio.run(utils.get_weather_forecast(ZoneInfo('Europe/Rome'), location=location)) is None
    assert not requests


@pytest.mark.parametrize('observed_at', [
    '2026-10-30 12:30 PM',  # stale cached sunny/27-degree response
    '2026-11-01 09:00 AM',  # four hours old locally
    '2026-11-01 01:30 PM',  # implausibly far in the future
    'invalid timestamp',
])
def test_stale_or_invalid_observations_are_not_used(weather_service, observed_at):
    configure, _ = weather_service
    configure({'current_condition': [{
        'weatherDesc': [{'value': 'Sunny'}], 'temp_C': '27',
        'localObsDateTime': observed_at,
    }]})

    assert asyncio.run(utils.get_weather_forecast(ZoneInfo('Europe/Rome'), location='Rome, Italy')) is None


@pytest.mark.parametrize(('forecast_date', 'observation_time', 'expected'), [
    ('2026-11-01', '11:30 AM', 'Overcast'),
    ('2026-10-31', '11:30 AM', None),
    ('2026-11-02', '11:30 AM', None),
    ('2026-11-01', '06:00 AM', None),
])
def test_responses_without_local_timestamp_need_fresh_date_and_utc_time(
        weather_service, forecast_date, observation_time, expected):
    configure, _ = weather_service
    configure({
        'current_condition': [{
            'weatherDesc': [{'value': 'Overcast'}], 'observation_time': observation_time,
        }],
        'weather': [{'date': forecast_date}],
    })

    assert asyncio.run(utils.get_weather_forecast(
        ZoneInfo('Europe/Rome'), location='Rome, Italy')) == expected


def test_utc_observation_time_can_be_before_midnight(weather_service):
    configure, _ = weather_service
    configure({
        'current_condition': [{
            'weatherDesc': [{'value': 'Clear'}], 'observation_time': '11:30 PM',
        }],
        'weather': [{'date': '2026-11-01'}],
    }, now='2026-11-01T00:15:00+00:00')

    assert asyncio.run(utils.get_weather_forecast(
        ZoneInfo('Europe/Rome'), location='Rome, Italy')) == 'Clear'


@pytest.mark.parametrize(('data', 'status'), [
    ({}, 503),
    ({'current_condition': []}, 200),
    ({'current_condition': [{'weatherDesc': [{'value': 'Sunny'}]}]}, 200),
])
def test_unavailable_or_unverifiable_weather_is_skipped(weather_service, data, status):
    configure, _ = weather_service
    configure(data, status=status)

    assert asyncio.run(utils.get_weather_forecast(ZoneInfo('Europe/Rome'), location='Rome, Italy')) is None
