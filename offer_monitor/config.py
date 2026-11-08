from pathlib import Path
from typing import Literal, get_args
from pydantic import BaseModel, ConfigDict, Field
from .errors import ConfigError
from .settings import read_yaml, require_file, validate_file

CONFIG_DIR = Path("data/configs")
CONFIG_FILE = "config.yaml"
CONFIG_PATH = CONFIG_DIR / CONFIG_FILE

SourceProviderKey = Literal["profesia", "linkedin"]
SOURCE_PROVIDER_KEYS = get_args(SourceProviderKey)


class PedanticForbidModel(BaseModel):
    """
    Shared Pydantic base class.
    Key not declared on a model is an error, not silently ignored.
    """

    model_config = ConfigDict(extra="forbid")


class TelegramConfig(PedanticForbidModel):
    token: str | None = None
    # old JSON subscriber list, imported into the database once; kept so existing configs still load
    subscribers: Path = Path("subscribers.json")


class ModelConfig(PedanticForbidModel):
    # the matching langchain-* package must be installed;
    provider: Literal["anthropic", "openai", "google"]
    # model_name; null uses the provider's default;
    name: str | None = None
    temperature: float = Field(0.0, ge=0, le=2)
    max_tokens: int = Field(8192, ge=1)
    # null or empty uses the provider's env var; with neither set, verdicts are disabled
    api_key: str | None = None


class Config(PedanticForbidModel):
    # sources to run; 
    # each one's file must sit next to this config;
    providers: list[SourceProviderKey] = Field(default_factory=list)
    db: Path = Path("jobs.db")
    backstory: Path = Path("backstory.md")
    interval: float = Field(60, gt=0)  # minutes between scrapes
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    # no `model` section: verdicts are disabled
    model: ModelConfig | None = None


def parse_config(data: dict, source: str | Path = CONFIG_FILE) -> Config:
    """
    Parse the shared settings from decoded YAML; 
    a missing `providers` list gets a clear error
    """
    # no default here: a config without the list would otherwise run with no sources and say nothing
    if "providers" not in data:
        raise ConfigError(
            f"{source}: providers: list the sources to run, e.g. [profesia, linkedin]"
        )
    return validate_file(source, Config, data)


# Load config.yaml from CONFIG_PATH. Relative paths resolve against the config's folder.
def load_config() -> Config:
    # read the global at call time, so tests can point it elsewhere
    path = CONFIG_PATH
    require_file(path)
    config = parse_config(read_yaml(path), path)
    if not config.db.is_absolute():
        config.db = path.parent / config.db
    if not config.backstory.is_absolute():
        config.backstory = path.parent / config.backstory
    if not config.telegram.subscribers.is_absolute():
        config.telegram.subscribers = path.parent / config.telegram.subscribers
    return config
