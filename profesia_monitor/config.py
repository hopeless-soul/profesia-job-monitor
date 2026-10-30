# Load data/config.json. See data/config.example.json for the format.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .scraper import Query

_QUERY_KEYS = {"keyword", "segments", "remote_work", "min_salary", "salary_period", "count_days"}
_TOP_KEYS = {"$schema", "db", "backstory", "pages", "delay", "count_days", "searches", "telegram", "interval", "model", "linkedin"}
_TELEGRAM_KEYS = {"token", "subscribers"}
_MODEL_KEYS = {"provider", "name", "temperature", "max_tokens", "api_key"}
_LINKEDIN_KEYS = {"searches", "date_posted", "max_pages"}
_LINKEDIN_SEARCH_KEYS = {"keywords", "work_type", "experience_level", "job_type"}

# Values the LinkedIn MCP server's search_jobs accepts
_DATE_POSTED = {"past_hour", "past_24_hours", "past_week", "past_month"}
_LINKEDIN_FILTERS = {
    "work_type": {"on_site", "remote", "hybrid"},
    "experience_level": {"internship", "entry", "associate", "mid_senior", "director", "executive"},
    "job_type": {"full_time", "part_time", "contract", "temporary", "volunteer", "internship", "other"},
}


class ConfigError(ValueError):
    pass


@dataclass
class TelegramConfig:
    token: str | None = None  # falls back to the TELEGRAM_BOT_TOKEN env var
    subscribers: Path = Path("subscribers.json")

@dataclass
class ModelConfig:
    # "anthropic" or "openai". The matching langchain-* package must be installed
    # and the provider's API key present in the environment.
    provider: str = "anthropic"
    # Model name. Sensible default per provider is filled in when left blank.
    name: str | None = None
    temperature: float = 0.0
    max_tokens: int = 8192  # thinking models spend part of this on reasoning
    api_key: str | None = None  # falls back to the provider's env var


# One LinkedIn search. Filters are comma-separated MCP values, e.g. work_type "remote,hybrid".
# Put the location in the keywords ("python developer in Slovakia"): LinkedIn ignores a separate location.
@dataclass
class LinkedInSearch:
    keywords: str
    work_type: str | None = None
    experience_level: str | None = None
    job_type: str | None = None


@dataclass
class LinkedInConfig:
    searches: list[LinkedInSearch]
    date_posted: str = "past_24_hours"
    max_pages: int = 1  # result pages per search (1-10)


@dataclass
class Config:
    searches: list[Query] = field(default_factory=list)
    db: Path = Path("jobs.db")  # SQLite offer store
    backstory: Path = Path("backstory.md")  # candidate profile for the offer reviewer
    pages: int = 3
    delay: float = 1.5
    interval: float = 60  # minutes between scrapes
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    linkedin: LinkedInConfig | None = None  # None = LinkedIn source off


def _count_days(value: object, where: str) -> int | None:
    if value is not None and (not isinstance(value, int) or value < 1):
        raise ConfigError(f"{where}: expected a positive integer (days) or null")
    return value


def _query(raw: object, where: str, default_days: int | None) -> Query:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected an object")
    if unknown := raw.keys() - _QUERY_KEYS:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    keyword = raw.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        raise ConfigError(f"{where}.keyword: required non-empty string")
    segments = raw.get("segments", [])
    if not isinstance(segments, list) or not all(isinstance(s, str) for s in segments):
        raise ConfigError(f"{where}.segments: expected a list of strings")
    remote = raw.get("remote_work")
    if remote not in (None, 0, 1, 2):
        raise ConfigError(f"{where}.remote_work: expected 0, 1, 2 or null")
    min_salary = raw.get("min_salary")
    if min_salary is not None and (not isinstance(min_salary, int) or min_salary <= 0):
        raise ConfigError(f"{where}.min_salary: expected a positive integer or null")
    period = raw.get("salary_period", "m")
    if period not in ("m", "h"):
        raise ConfigError(f"{where}.salary_period: expected 'm' or 'h'")
    days = _count_days(raw.get("count_days", default_days), f"{where}.count_days")

    query = Query(keyword, segments, remote, min_salary, period, days)
    try:
        query.url()
    except ValueError as e:
        raise ConfigError(f"{where}.segments: {e}") from None
    return query


def _telegram(raw: object) -> TelegramConfig:
    if raw is None:
        return TelegramConfig()
    if not isinstance(raw, dict):
        raise ConfigError("telegram: expected an object")
    if unknown := raw.keys() - _TELEGRAM_KEYS:
        raise ConfigError(f"telegram: unknown keys {sorted(unknown)}")
    token = raw.get("token")
    if token is not None and not isinstance(token, str):
        raise ConfigError("telegram.token: expected a string or null")
    subscribers = raw.get("subscribers", "subscribers.json")
    if not isinstance(subscribers, str) or not subscribers:
        raise ConfigError("telegram.subscribers: expected a file path")
    return TelegramConfig(token or None, Path(subscribers))


def _model(raw: object) -> ModelConfig:
    if raw is None:
        return ModelConfig()
    if not isinstance(raw, dict):
        raise ConfigError("model: expected an object")
    if unknown := raw.keys() - _MODEL_KEYS:
        raise ConfigError(f"model: unknown keys {sorted(unknown)}")
    provider = raw.get("provider", "anthropic")
    if not isinstance(provider, str) or provider not in ("anthropic", "openai", "google"):
        raise ConfigError("model.provider: expected 'anthropic', 'openai', or 'google'")
    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        raise ConfigError("model.name: expected a string or null")
    temperature = raw.get("temperature", 0.0)
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or not (0 <= temperature <= 2):
        raise ConfigError("model.temperature: expected a number between 0 and 2")
    max_tokens = raw.get("max_tokens", 8192)
    if not isinstance(max_tokens, int) or max_tokens < 1:
        raise ConfigError("model.max_tokens: expected a positive integer")
    # optional API key, empty string means "use the env var"
    api_key = raw.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise ConfigError("model.api_key: expected a string or null")
    return ModelConfig(provider, name, float(temperature), max_tokens, api_key or None)


def _linkedin_search(raw: object, where: str) -> LinkedInSearch:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected an object")
    if unknown := raw.keys() - _LINKEDIN_SEARCH_KEYS:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    keywords = raw.get("keywords")
    if not isinstance(keywords, str) or not keywords.strip():
        raise ConfigError(f"{where}.keywords: required non-empty string")

    # each filter is a comma-separated list of allowed values
    filters = {}
    for key, allowed in _LINKEDIN_FILTERS.items():
        value = raw.get(key)
        if value is not None and (
            not isinstance(value, str) or not {v.strip() for v in value.split(",")} <= allowed
        ):
            raise ConfigError(f"{where}.{key}: expected comma-separated values from {sorted(allowed)}")
        filters[key] = value
    return LinkedInSearch(keywords, **filters)


def _linkedin(raw: object) -> LinkedInConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("linkedin: expected an object")
    if unknown := raw.keys() - _LINKEDIN_KEYS:
        raise ConfigError(f"linkedin: unknown keys {sorted(unknown)}")
    searches = raw.get("searches")
    if not isinstance(searches, list) or not searches:
        raise ConfigError("linkedin.searches: expected a non-empty list")
    date_posted = raw.get("date_posted", "past_24_hours")
    if date_posted not in _DATE_POSTED:
        raise ConfigError(f"linkedin.date_posted: expected one of {sorted(_DATE_POSTED)}")
    max_pages = raw.get("max_pages", 1)
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 10:
        raise ConfigError("linkedin.max_pages: expected an integer from 1 to 10")
    return LinkedInConfig(
        [_linkedin_search(s, f"linkedin.searches[{i}]") for i, s in enumerate(searches)], date_posted, max_pages
    )


def parse_config(data: object) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config: expected an object")
    if unknown := data.keys() - _TOP_KEYS:
        raise ConfigError(f"config: unknown keys {sorted(unknown)}")

    searches = data.get("searches", [])
    if not isinstance(searches, list) or not searches:
        raise ConfigError("searches: expected a non-empty list")
    pages = data.get("pages", 3)
    if not isinstance(pages, int) or pages < 1:
        raise ConfigError("pages: expected a positive integer")
    delay = data.get("delay", 1.5)
    if not isinstance(delay, (int, float)) or delay < 0:
        raise ConfigError("delay: expected a non-negative number")
    interval = data.get("interval", 60)
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
        raise ConfigError("interval: expected a positive number of minutes")
    # database and backstory file paths
    db = data.get("db", "jobs.db")
    if not isinstance(db, str) or not db:
        raise ConfigError("db: expected a file path")
    backstory = data.get("backstory", "backstory.md")
    if not isinstance(backstory, str) or not backstory:
        raise ConfigError("backstory: expected a file path")
    days = _count_days(data.get("count_days"), "count_days")

    return Config(
        searches=[_query(s, f"searches[{i}]", days) for i, s in enumerate(searches)],
        db=Path(db),
        backstory=Path(backstory),
        pages=pages,
        delay=float(delay),
        interval=float(interval),
        telegram=_telegram(data.get("telegram")),
        model=_model(data.get("model")),
        linkedin=_linkedin(data.get("linkedin")),
    )


def load_config(path: Path) -> Config:
    # Load and validate a config file. Relative paths resolve against the config's folder.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON ({e})") from None
    config = parse_config(data)
    if not config.db.is_absolute():
        config.db = path.parent / config.db
    if not config.backstory.is_absolute():
        config.backstory = path.parent / config.backstory
    if not config.telegram.subscribers.is_absolute():
        config.telegram.subscribers = path.parent / config.telegram.subscribers
    return config
