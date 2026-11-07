from pathlib import Path
from typing import Literal, get_args
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .errors import ConfigError
from .settings import read_yaml, validate_file

CONFIG_FILE = "config.yaml"

LLM_PROVIDERS = ("anthropic", "openai", "google") # Default: 1st item
ProviderKey = Literal["profesia", "linkedin"]
PROVIDER_KEYS = get_args(ProviderKey)

class PedanticForbidModel(BaseModel):
    """
    Shared Pydantic base class. 
    Key not declared on a model is an error, not silently ignored.
    """
    model_config = ConfigDict(extra="forbid")

class TelegramConfig(PedanticForbidModel):
    token: str | None = None
    subscribers: Path = Path("subscribers.json")


class ModelConfig(PedanticForbidModel):
    # LLM provider; the matching langchain-* package must be installed
    provider: Literal["anthropic", "openai", "google"] = LLM_PROVIDERS[0]
    name: str | None = None # model_name; null uses the provider's default
    temperature: float = Field(0.0, ge=0, le=2)
    max_tokens: int = Field(8192, ge=1)
    api_key: str | None = None

    # an empty key means "use the env var"
    @field_validator("api_key")
    @classmethod
    def _empty_key_is_none(cls, value: str | None) -> str | None:
        return value or None


class Config(PedanticForbidModel):
    # sources to run; each one's file must sit next to this config (read by providers.build_providers).
    providers: list[ProviderKey] = Field(default_factory=list)
    db: Path = Path("jobs.db")  # SQLite offer store
    backstory: Path = Path("backstory.md")  # candidate profile for the offer reviewer
    interval: float = Field(60, gt=0)  # minutes between scrapes
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)


# Parse the shared settings from decoded YAML; a missing `providers` list gets a clear error
def parse_config(data: dict, source: str | Path = CONFIG_FILE) -> Config:
    # no default here: a config without the list would otherwise run with no sources and say nothing
    if "providers" not in data:
        raise ConfigError(f"{source}: providers: list the sources to run, e.g. [profesia, linkedin]")
    return validate_file(source, Config, data)


# Load config.yaml. Relative paths resolve against the config's folder.
def load_config(path: Path) -> Config:
    config = parse_config(read_yaml(path), path)
    if not config.db.is_absolute():
        config.db = path.parent / config.db
    if not config.backstory.is_absolute():
        config.backstory = path.parent / config.backstory
    if not config.telegram.subscribers.is_absolute():
        config.telegram.subscribers = path.parent / config.telegram.subscribers
    return config
