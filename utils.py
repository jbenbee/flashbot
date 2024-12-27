import base64
import os
from pathlib import Path
import time
import openai


async def get_assistant_response(interface, query, uilang, model_base, model_substitute, response_format=None, validation_cls=None):

    openai_key = os.getenv('OPENAI_KEY')
    if openai_key is None:
        with open(Path('api_keys/openai_api.txt'), 'r') as fp:
                lines = fp.readlines()
                openai_key = lines[0].strip()
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

