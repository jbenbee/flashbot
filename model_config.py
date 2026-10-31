"""Validated model settings shared by all generation workflows."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


MODEL_CONFIG_PATH = Path(__file__).resolve().parent / 'resources' / 'models.yaml'


class ConfigSection(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ClientConfig(ConfigSection):
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)


class ResponseModelConfig(ConfigSection):
    model: str = Field(min_length=1)
    params: dict[str, Any]

    @model_validator(mode='after')
    def validate_params(self):
        reserved = {'model', 'input', 'instructions', 'stream', 'background'} & self.params.keys()
        if reserved:
            raise ValueError(f'Assistant params cannot override: {", ".join(sorted(reserved))}')
        if 'text' in self.params and not isinstance(self.params['text'], dict):
            raise ValueError('Assistant text params must be a mapping')
        return self


class AssistantConfig(ConfigSection):
    model_base: ResponseModelConfig
    model_fallback: ResponseModelConfig
    max_attempts: int = Field(ge=1)
    retry_delay_seconds: float = Field(ge=0)


class SpeechConfig(ConfigSection):
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(gt=0)
    session: dict[str, Any]

    @model_validator(mode='after')
    def validate_session(self):
        if self.session.get('type') != 'realtime' or self.session.get('output_modalities') != ['audio']:
            raise ValueError('Speech requires a realtime session with audio output')
        if not isinstance(self.session.get('instructions'), str):
            raise ValueError('Speech session requires text instructions')
        audio = self.session.get('audio', {})
        output = audio.get('output', {}) if isinstance(audio, dict) else {}
        if not isinstance(output, dict) or output.get('format') != {'type': 'audio/pcm', 'rate': 24000}:
            raise ValueError('Speech WAV output requires audio/pcm at 24000 Hz')
        return self


class SongConfig(ConfigSection):
    model: str = Field(min_length=1)
    params: dict[str, Any]
    timeout_seconds: float = Field(gt=0)
    poll_interval_seconds: float = Field(ge=0)
    max_poll_attempts: int = Field(ge=1)

    @model_validator(mode='after')
    def validate_params(self):
        if {'model', 'prompt'} & self.params.keys():
            raise ValueError('Song params cannot override model or prompt')
        return self


class ModelConfig(ConfigSection):
    client: ClientConfig
    assistant: AssistantConfig
    speech: SpeechConfig
    song: SongConfig


@lru_cache(maxsize=1)
def load_model_config(config_path: Path = MODEL_CONFIG_PATH) -> ModelConfig:
    """Load once per process, resolving the default path relative to this module."""
    try:
        data = yaml.safe_load(Path(config_path).read_text(encoding='utf-8'))
        return ModelConfig.model_validate(data)
    except (OSError, yaml.YAMLError, ValueError) as error:
        raise ValueError(f'Invalid model configuration in {config_path}: {error}') from error
