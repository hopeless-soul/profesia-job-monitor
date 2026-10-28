# Job data model. Field layout mirrors schemas/jobs.schema.json.

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Period = Literal["month", "hour"]


@dataclass(frozen=True)
class Salary:
    raw: str
    min: float | None
    max: float | None
    currency: str | None
    period: Period | None

    @classmethod
    def from_dict(cls, d: dict) -> Salary:
        return cls(**d)


@dataclass
class Job:
    id: int
    title: str
    url: str
    company: str | None = None
    company_id: int | None = None
    location: str | None = None
    salary: Salary | None = None
    labels: list[str] = field(default_factory=list)
    posted_text: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Job:
        d = dict(d)
        if d.get("salary") is not None:
            d["salary"] = Salary.from_dict(d["salary"])
        return cls(**d)
