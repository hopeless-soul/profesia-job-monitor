# Load data/configs/config.yaml: the shared settings. Each job source has its own file next to it
# (profesia_sk.yaml, linkedin.yaml); a source is on when its file exists.

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

    db: Path = Path("jobs.db")  # SQLite offer store
    backstory: Path = Path("backstory.md")  # candidate profile for the offer reviewer
    interval: float = Field(60, gt=0)  # minutes between scrapes
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    profesia: profesia_config.ProfesiaConfig | None = None  # None = Profesia source off
    linkedin: linkedin_config.LinkedInConfig | None = None  # None = LinkedIn source off


# Parse the shared settings from decoded YAML; a key that moved to a provider file gets a clear error
def parse_config(data: dict, source: str | Path = CONFIG_FILE) -> Config:
    if moved := data.keys() & _MOVED_KEYS:
        key = sorted(moved)[0]
        raise ConfigError(f"{source}: '{key}' moved out of this file; put it in profesia_sk.yaml or linkedin.yaml")
    return validate_file(source, Config, data)


# Load config.yaml and every provider file beside it. Relative paths resolve against the config's folder.
def load_config(path: Path) -> Config:
    config = parse_config(read_yaml(path), path)
    if not config.db.is_absolute():
        config.db = path.parent / config.db
    if not config.backstory.is_absolute():
        config.backstory = path.parent / config.backstory
    if not config.telegram.subscribers.is_absolute():
        config.telegram.subscribers = path.parent / config.telegram.subscribers

    # a provider is on when its file sits next to the main config
    profesia_file = path.parent / profesia_config.CONFIG_FILE
    if profesia_file.exists():
        config.profesia = profesia_config.load_config(profesia_file)
    linkedin_file = path.parent / linkedin_config.CONFIG_FILE
    if linkedin_file.exists():
        config.linkedin = linkedin_config.load_config(linkedin_file)
    return config
