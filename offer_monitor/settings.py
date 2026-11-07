# Settings shared by the main config and every provider config: the retry policy, plus reading a YAML
# file and validating it with a pydantic model. Errors come out as ConfigError naming the file and field.

import yaml
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ConfigError

T = TypeVar("T", bound=BaseModel)


# How often run_once tries a source: `attempts` tries in total, `delay` seconds between them
class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(3, ge=1, le=10)
    delay: float = Field(30, ge=0)


# Fields every source config has: seconds between requests, and the retry policy
class SourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay: float = Field(1.5, ge=0)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


# Fail with a ConfigError if a config file is missing; names the example to copy. `context` says why the file is needed
def require_file(path: Path, context: str = "") -> None:
    if not path.exists():
        prefix = f"{context}: " if context else ""
        example = path.with_suffix(".example.yaml").name
        raise ConfigError(f"{prefix}{path} not found; copy {example} in that folder to {path.name}")


# Read a YAML file into a dict; an empty file is an empty dict, a syntax error names the line
def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        raise ConfigError(f"{path}: invalid YAML{where}: {getattr(e, 'problem', e)}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


# Turn a pydantic location such as ("searches", 0, "remote_work") into "searches[0].remote_work"
def _field_path(loc: tuple) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path or "(root)"


# Validate `data` with `model`; the first problem becomes a ConfigError naming the source and field
def validate_file(source: str | Path, model: type[T], data: dict) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        raise ConfigError(f"{source}: {_field_path(first['loc'])}: {first['msg']}") from None
