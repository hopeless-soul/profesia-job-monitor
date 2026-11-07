# Profesia.sk source config, read from data/configs/profesia_sk.yaml. See profesia_sk.example.yaml there.

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...settings import SourceSettings, read_yaml, validate_file
from .parser import BASE_URL

# File name next to the main config; read when the main config's `providers` lists "profesia"
CONFIG_FILE = "profesia_sk.yaml"

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


# One Profesia search: free-text keyword plus optional filters, each checked on load
class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1)  # free-text search (search_anywhere), e.g. "c#"
    # path-segment filters, e.g. ["bratislavsky-kraj", "informacne-technologie"]
    segments: list[str] = Field(default_factory=list)
    # 0 = on-site only, 1 = remote only, 2 = partially remote
    remote_work: Literal[0, 1, 2] | None = None
    min_salary: int | None = Field(None, gt=0)
    salary_period: Literal["m", "h"] = "m"
    # only offers posted in the last N days; values >= 31 behave like no filter
    count_days: int | None = Field(None, ge=1)

    # a keyword of only spaces is no keyword
    @field_validator("keyword")
    @classmethod
    def _keyword_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    # every path segment must be a lowercase slug, since it becomes part of the URL
    @field_validator("segments")
    @classmethod
    def _segments_are_slugs(cls, values: list[str]) -> list[str]:
        for segment in values:
            if not _SLUG_RE.match(segment):
                raise ValueError(f"invalid path segment: {segment!r}")
        return values

    # listing URL for this search
    def url(self) -> str:
        path = "".join(f"{s}/" for s in self.segments)
        return f"{BASE_URL}/praca/{path}"

    # query-string parameters for one result page
    def params(self, page: int) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "search_anywhere": self.keyword,
            "sort_by": "relevance",
            "page_num": page,
        }
        if self.remote_work is not None:
            params["remote_work"] = self.remote_work
        if self.min_salary is not None:
            params["salary"] = self.min_salary
            params["salary_period"] = self.salary_period
        if self.count_days is not None:
            params["count_days"] = self.count_days
        return params


# Profesia settings: searches, paging, and the file-level count_days that searches inherit
class ProfesiaConfig(SourceSettings):
    searches: list[Query] = Field(min_length=1)
    pages: int = Field(3, ge=1)  # listing pages per search
    count_days: int | None = Field(None, ge=1)

    # a search without its own count_days takes the file-level value; an explicit null stays null
    @model_validator(mode="after")
    def _inherit_count_days(self) -> "ProfesiaConfig":
        for query in self.searches:
            if "count_days" not in query.model_fields_set:
                query.count_days = self.count_days
        return self


# Parse the Profesia config from decoded YAML; errors name the file and field
def parse_config(data: dict) -> ProfesiaConfig:
    return validate_file(CONFIG_FILE, ProfesiaConfig, data)


# Load and validate the Profesia config file
def load_config(path: Path) -> ProfesiaConfig:
    return validate_file(path, ProfesiaConfig, read_yaml(path))
