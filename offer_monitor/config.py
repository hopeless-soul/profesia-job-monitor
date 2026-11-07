# Load data/configs/config.yaml: the shared settings. Each job source has its own file next to it
# (profesia_sk.yaml, linkedin.yaml); a source is on when `providers` lists it.

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import ConfigError
from .providers.linkedin import config as linkedin_config
from .providers.profesia_sk import config as profesia_config
from .settings import read_yaml, validate_file

CONFIG_FILE = "config.yaml"

# Keys that now live in a provider file; finding one here is a clear error, not an unknown key
_MOVED_KEYS = {"searches", "pages", "delay", "count_days", "retry", "linkedin", "profesia"}

# Name in `providers` -> the config module of that source (its CONFIG_FILE and load_config)
_PROVIDER_CONFIGS = {"profesia": profesia_config, "linkedin": linkedin_config}


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = None  # falls back to the TELEGRAM_BOT_TOKEN env var
    subscribers: Path = Path("subscribers.json")


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "anthropic", "openai" or "google"; the matching langchain-* package must be installed
    provider: Literal["anthropic", "openai", "google"] = "anthropic"
    # model name; null uses the provider's default
    name: str | None = None
    temperature: float = Field(0.0, ge=0, le=2)
    max_tokens: int = Field(8192, ge=1)  # thinking models spend part of this on reasoning
    api_key: str | None = None  # falls back to the provider's env var

    # an empty key means "use the env var"
    @field_validator("api_key")
    @classmethod
    def _empty_key_is_none(cls, value: str | None) -> str | None:
        return value or None


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # sources to run; each one's file must sit next to this config. The file requires the key (see parse_config).
    providers: list[Literal["profesia", "linkedin"]] = Field(default_factory=list)
    db: Path = Path("jobs.db")  # SQLite offer store
    backstory: Path = Path("backstory.md")  # candidate profile for the offer reviewer
    interval: float = Field(60, gt=0)  # minutes between scrapes
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    profesia: profesia_config.ProfesiaConfig | None = None  # None = Profesia source off
    linkedin: linkedin_config.LinkedInConfig | None = None  # None = LinkedIn source off


# Parse the shared settings from decoded YAML; a key that moved to a provider file, or a missing
# `providers` list, gets a clear error
def parse_config(data: dict, source: str | Path = CONFIG_FILE) -> Config:
    if moved := data.keys() & _MOVED_KEYS:
        key = sorted(moved)[0]
        raise ConfigError(f"{source}: '{key}' moved out of this file; put it in profesia_sk.yaml or linkedin.yaml")
    # no default here: a config without the list would otherwise run with no sources and say nothing
    if "providers" not in data:
        raise ConfigError(f"{source}: providers: list the sources to run, e.g. [profesia, linkedin]")
    return validate_file(source, Config, data)


# Load config.yaml and the file of every provider it lists. Relative paths resolve against the config's folder.
def load_config(path: Path) -> Config:
    config = parse_config(read_yaml(path), path)
    if not config.db.is_absolute():
        config.db = path.parent / config.db
    if not config.backstory.is_absolute():
        config.backstory = path.parent / config.backstory
    if not config.telegram.subscribers.is_absolute():
        config.telegram.subscribers = path.parent / config.telegram.subscribers

    # load only the listed providers (the field has the same name); a listed one without its file is an error
    for name in config.providers:
        module = _PROVIDER_CONFIGS[name]
        file = path.parent / module.CONFIG_FILE
        if not file.exists():
            raise ConfigError(f"{path}: providers lists '{name}', but {file} is missing")
        setattr(config, name, module.load_config(file))
    return config
