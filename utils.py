import base64
import json
import os
from pathlib import Path
import random
import time
import openai
import requests

async def get_assistant_response(interface, query, uilang, model_base, model_substitute, response_format=None, validation_cls=None):

    openai_key = os.getenv('OPENAI_KEY')
    client = openai.OpenAI(api_key=openai_key, timeout=20.0, max_retries=0)

    nattempts = 0
    messages = [
                {"role": "system", "content": interface["You are a great language teacher"][uilang]},
                {"role": "user", "content": query},
            ]
    model = model_base
    max_attempts = 3
    while nattempts < max_attempts:
        nattempts += 1
        try:
            print(f'Sending a request to chatgpt ({model})...')
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=600,
                response_format=response_format
            )
            break
        except openai.OpenAIError as e:
            print(e)
            time.sleep(nattempts)
        if nattempts == max_attempts - 1:
            model = model_substitute
    print('Done.')

    if nattempts == 3:
        print('The assistant raised an error.')
        raise ValueError('The model could not respond in required format')
    if response.choices[0].message.refusal:
        print('The assistant refused to respond.')
        raise ValueError('The model refused to respond')

    if validation_cls is not None:
        validated_resp = validation_cls.model_validate_json(response.choices[0].message.content)
    else:
        validated_resp = response.choices[0].message.content

    return validated_resp


def get_audio(query, lang, file_path):

    openai_key = os.getenv('OPENAI_KEY')
    if openai_key is None:
        with open(Path('api_keys/openai_api.txt'), 'r') as fp:
                lines = fp.readlines()
                openai_key = lines[0].strip()
    client = openai.OpenAI(api_key=openai_key, timeout=20.0, max_retries=0)

    completion = client.chat.completions.create(
        model="gpt-4o-audio-preview",
        modalities=["text", "audio"],
        audio={"voice": "alloy", "format": "wav"},
        messages=[
            {
                "role": "user",
                "content": f'Pronounce this phrase {lang}: {query}'
            }
        ]
    )

    wav_bytes = base64.b64decode(completion.choices[0].message.audio.data)
    with open(file_path, "wb") as f:
        f.write(wav_bytes)

def download_file(aurl, fpath):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
        "Referer": "https://aiquickdraw.com/",
    }

    r = requests.get(aurl, headers=headers, stream=True)
    r.raise_for_status()

    with open(fpath, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

def generate_song_of_the_day(word, lang, dirpath):
    print(f'Generating song of the day')
    BASE_URL = "https://api.sunoapi.org"
    url = f"{BASE_URL}/api/v1/generate"

    with open('resources/song_genres.json', 'r') as fp:
        genres = json.loads(fp.read())

    genre_prompt = ''
    if genres:
        genre = random.sample(genres, 1)
        genre_prompt = f" Genre: {genre[0]}."
    payload = {
        "customMode": False,
        "model": "V4_5",
        "prompt": f"Create a song in {lang} that uses in its lyrics the word '{word}'.{genre_prompt}",
        "instrumental": False,
        "callBackUrl": "test.url"
    }
    suno_api_key = os.getenv('SUNO_API_KEY')
    headers = {
        "Authorization": f"Bearer {suno_api_key}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    task_id = response.json()['data']["taskId"]

    print(f'Sent generation request, waiting...')

    url = f"{BASE_URL}/api/v1/generate/record-info"
    n_iter = 1
    while True:
        response = requests.get(url, headers=headers, params={"taskId": task_id})
        response.raise_for_status()

        data = response.json()
        status = data['data']['status']
        
        if status == "SUCCESS":
            print(f'Song generation complete')
            
            audio_url_field = 'audioUrl'
            audio_data = [{'audio_url': d[audio_url_field], 'audio_file_path': dirpath / f'f{didx}.mp3'}
                          for didx, d in enumerate(data['data']['response']['sunoData'])]
            data = {'img_path': dirpath / f'img.jpg', 'img_url': data['data']['response']['sunoData'][0]['imageUrl'], 
                    'lyrics': data['data']['response']['sunoData'][0]['prompt'], 'title': data['data']['response']['sunoData'][0]['title'], 'audio_data': audio_data}

            download_file(data['img_url'], str(data['img_path']))
            
            for aurl in audio_data:
                download_file(aurl['audio_url'], str(aurl['audio_file_path']))

            return data
        elif status == "PENDING" or 'SUCCESS' in status:
            print("Still generating...")
            time.sleep(5 * n_iter)
            n_iter += 1
            if n_iter > 20:
                raise Exception('Song generation took too long.')
        elif "FAIL" in status:
            raise Exception(status)