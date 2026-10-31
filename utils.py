import asyncio
import base64
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import random
import wave
import httpx
import openai
from model_config import load_model_config
from logging_config import logged_operation

logger = logging.getLogger(__name__)

@logged_operation
async def get_assistant_response(interface, query, uilang, model_base=None, model_fallback=None, response_format=None, validation_cls=None):
    """Generate text through Responses, accepting the callers' existing format specs."""

    config = load_model_config()
    assistant = config.assistant
    openai_key = os.getenv('OPENAI_KEY')
    client = openai.AsyncOpenAI(api_key=openai_key, timeout=config.client.timeout_seconds,
                                max_retries=config.client.max_retries)

    nattempts = 0
    max_attempts = assistant.max_attempts
    try:
        while nattempts < max_attempts:
            nattempts += 1
            use_fallback = nattempts == max_attempts and max_attempts > 1
            model_config = assistant.model_fallback if use_fallback else assistant.model_base
            model = (model_fallback if use_fallback else model_base) or model_config.model
            params = deepcopy(model_config.params)
            text_config = params.setdefault('text', {})
            if response_format is not None:
                if response_format['type'] == 'json_schema':
                    text_config['format'] = {'type': 'json_schema', **response_format['json_schema']}
                else:
                    text_config['format'] = response_format
            try:
                logger.info('Assistant request model=%s attempt=%s max_attempts=%s', model, nattempts, max_attempts)
                response = await client.responses.create(
                    model=model,
                    instructions=interface["You are a great language teacher"][uilang],
                    input=query,
                    **params,
                )
                break
            except openai.OpenAIError as e:
                logger.warning('Assistant request failed model=%s attempt=%s max_attempts=%s error_type=%s',
                               model, nattempts, max_attempts, type(e).__name__, exc_info=True)
                if nattempts == max_attempts:
                    raise ValueError('The model could not respond in required format') from e
                await asyncio.sleep(assistant.retry_delay_seconds * nattempts)
    finally:
        await client.close()

    if any(
        content.type == 'refusal'
        for item in response.output if item.type == 'message'
        for content in item.content
    ):
        logger.warning('Assistant response refused model=%s', model)
        raise ValueError('The model refused to respond')

    if response.status != 'completed' or not response.output_text:
        logger.warning('Assistant response incomplete model=%s status=%s', model, response.status)
        raise ValueError('The model could not respond in required format')

    if validation_cls is not None:
        validated_resp = validation_cls.model_validate_json(response.output_text)
    else:
        validated_resp = response.output_text

    return validated_resp


async def get_weather_forecast(tz):
    """Get a short description of the current weather for the city derived from the user's timezone.

    Uses the free wttr.in service (no API key required). Returns None if the weather could not be fetched.
    """
    try:
        city = str(tz).split('/')[-1].replace('_', ' ')
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f'https://wttr.in/{city}?format=j1')
            response.raise_for_status()
            data = response.json()

        current = data['current_condition'][0]
        today = data['weather'][0]

        condition = current['weatherDesc'][0]['value']
        temp = current['temp_C']
        min_temp = today['mintempC']
        max_temp = today['maxtempC']

        logger.info('Weather forecast fetched')
        return f'{condition}, {temp}°C (today: {min_temp}-{max_temp}°C)'
    except Exception:
        logger.warning('Weather forecast unavailable; continuing without weather', exc_info=True)
        return None


@logged_operation
async def get_audio(query, lang, file_path):
    """Read the supplied text aloud with Realtime and save a PCM WAV file."""

    config = load_model_config()
    speech = config.speech
    logger.info('Speech requested model=%s language=%s', speech.model, lang)
    session = deepcopy(speech.session)
    session['instructions'] = session['instructions'].replace('{lang}', lang)
    openai_key = os.getenv('OPENAI_KEY')
    if openai_key is None:
        key_file = await asyncio.to_thread(Path('api_keys/openai_api.txt').read_text)
        openai_key = key_file.splitlines()[0].strip()
    client = openai.AsyncOpenAI(api_key=openai_key, timeout=config.client.timeout_seconds,
                                max_retries=config.client.max_retries)

    audio_chunks = []
    try:
        # HTTP timeouts do not bound a WebSocket session's event stream.
        async with asyncio.timeout(speech.timeout_seconds):
            async with client.realtime.connect(model=speech.model) as connection:
                await connection.session.update(session=session)
                await connection.conversation.item.create(item={
                    'type': 'message',
                    'role': 'user',
                    'content': [{'type': 'input_text', 'text': query}],
                })
                await connection.response.create()

                async for event in connection:
                    if event.type == 'response.output_audio.delta':
                        audio_chunks.append(base64.b64decode(event.delta, validate=True))
                    elif event.type == 'error':
                        raise openai.OpenAIError(f'Speech generation failed: {event.error.message}')
                    elif event.type == 'response.done':
                        if event.response.status != 'completed':
                            raise ValueError(f'Speech generation ended: {event.response.status}')
                        break
                else:
                    raise ValueError('Speech connection closed before the response completed')
    finally:
        await client.close()

    pcm_data = b''.join(audio_chunks)
    if not pcm_data or len(pcm_data) % 2:
        raise ValueError('The model returned empty or invalid PCM audio')
    await asyncio.to_thread(_write_pronunciation_wav, file_path, pcm_data)
    logger.info('Speech saved pcm_bytes=%s', len(pcm_data))


def _write_pronunciation_wav(file_path, pcm_data):
    with wave.open(str(file_path), 'wb') as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(24000)
        audio_file.writeframes(pcm_data)

@logged_operation
async def download_file(client, aurl, fpath):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
        "Referer": "https://aiquickdraw.com/",
    }

    async with client.stream("GET", aurl, headers=headers) as r:
        r.raise_for_status()

        file = await asyncio.to_thread(open, fpath, "wb")
        try:
            async for chunk in r.aiter_bytes(1024 * 1024):
                await asyncio.to_thread(file.write, chunk)
        finally:
            await asyncio.to_thread(file.close)

@logged_operation
async def generate_song_of_the_day(prompt, dirpath, *, style=None):
    song = load_model_config().song
    logger.info('Song generation requested model=%s', song.model)
    BASE_URL = "https://api.sunoapi.org"
    url = f"{BASE_URL}/api/v1/generate"

    payload = {
        **deepcopy(song.params),
        "model": song.model,
        "prompt": prompt,
    }
    # V6 requires a reference input in non-custom mode, even with a prompt.
    # Use the selected genre; prompt-only callers can use the idea as style.
    if song.model in {'V6', 'V6_MINI', 'V6_WILD'} and not payload.get('customMode'):
        if style:
            payload['style'] = style
        elif not any(payload.get(key) for key in ('style', 'lyrics', 'imageUrls', 'videoUrls', 'audioUrls')):
            payload['style'] = prompt[:1000]
    suno_api_key = os.getenv('SUNO_API_KEY')
    headers = {
        "Authorization": f"Bearer {suno_api_key}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=song.timeout_seconds) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        task_id = response.json()['data']["taskId"]

        logger.info('Song generation submitted task_id=%s', task_id)

        url = f"{BASE_URL}/api/v1/generate/record-info"
        n_iter = 1
        while True:
            response = await client.get(url, headers=headers, params={"taskId": task_id})
            response.raise_for_status()

            data = response.json()
            status = data['data']['status']

            if status == "SUCCESS":
                logger.info('Song generation succeeded task_id=%s', task_id)

                tracks = data['data']['response']['sunoData']
                if not tracks:
                    raise ValueError('Song generation returned no tracks.')
                audio_data = [{
                    'audio_url': track.get('audioUrl') or track['audio_url'],
                    'audio_file_path': dirpath / f'f{index}.mp3',
                    'title': track['title'],
                    'lyrics': track.get('lyrics') or track.get('prompt') or '',
                } for index, track in enumerate(tracks)]
                data = {
                    'img_path': dirpath / 'img.jpg',
                    'img_url': tracks[0].get('imageUrl') or tracks[0]['image_url'],
                    'title': tracks[0]['title'],
                    'audio_data': audio_data,
                }

                await asyncio.gather(
                    download_file(client, data['img_url'], str(data['img_path'])),
                    *[download_file(client, aurl['audio_url'], str(aurl['audio_file_path'])) for aurl in audio_data]
                )

                return data
            elif status == "PENDING" or 'SUCCESS' in status:
                logger.debug('Song generation pending task_id=%s attempt=%s status=%s', task_id, n_iter, status)
                await asyncio.sleep(song.poll_interval_seconds * n_iter)
                n_iter += 1
                if n_iter > song.max_poll_attempts:
                    raise Exception('Song generation took too long.')
            elif "FAIL" in status:
                raise Exception(status)
