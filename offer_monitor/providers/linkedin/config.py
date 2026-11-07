# LinkedIn source config, read from data/configs/linkedin.yaml. See linkedin.example.yaml there.

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from ...settings import SourceSettings, read_yaml, validate_file

# File name next to the main config; the source is off when the file is missing
CONFIG_FILE = "linkedin.yaml"

# Values the LinkedIn MCP server's search_jobs accepts, per filter
_FILTERS = {
    "work_type": {"on_site", "remote", "hybrid"},
    "experience_level": {"internship", "entry", "associate", "mid_senior", "director", "executive"},
    "job_type": {"full_time", "part_time", "contract", "temporary", "volunteer", "internship", "other"},
}


# One LinkedIn search. Filters are comma-separated MCP values, e.g. work_type "remote,hybrid".
# Put the location in the keywords ("python developer in Slovakia"): LinkedIn ignores a separate location.
class LinkedInSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: str = Field(min_length=1)
    work_type: str | None = None
    experience_level: str | None = None
    job_type: str | None = None

    # a keyword of only spaces is no keyword
    @field_validator("keywords")
    @classmethod
    def _keywords_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    # each filter is a comma-separated list; every value must be one the server accepts
    @field_validator("work_type", "experience_level", "job_type")
    @classmethod
    def _filter_values(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return value
        allowed = _FILTERS[info.field_name]
        if not {v.strip() for v in value.split(",")} <= allowed:
            raise ValueError(f"expected comma-separated values from {sorted(allowed)}")
        return value


# LinkedIn settings: searches, listing window and paging, and the shared delay and retry fields
class LinkedInConfig(SourceSettings):
    searches: list[LinkedInSearch] = Field(min_length=1)
    date_posted: Literal["past_hour", "past_24_hours", "past_week", "past_month"] = "past_24_hours"
    max_pages: int = Field(1, ge=1, le=10)  # result pages per search


# Parse the LinkedIn config from decoded YAML; errors name the file and field
def parse_config(data: dict) -> LinkedInConfig:
    return validate_file(CONFIG_FILE, LinkedInConfig, data)


# Load and validate the LinkedIn config file
def load_config(path: Path) -> LinkedInConfig:
    return validate_file(path, LinkedInConfig, read_yaml(path))
