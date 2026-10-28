# Job data model. Stored in SQLite by store.py; legacy jobs.json used the same field names.

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Period = Literal["month", "hour"]


# Parsed salary label of an offer
@dataclass(frozen=True)
class Salary:
    raw: str
    min: float | None
    max: float | None
    currency: str | None
    period: Period | None

    # build from a stored dict
    @classmethod
    def from_dict(cls, d: dict) -> Salary:
        return cls(**d)


# AI verdict on an offer, produced by the orchestrator agent
@dataclass(frozen=True)
class Verdict:
    tag: str  # GO / STRETCH / SKIP / UNREACHABLE
    content: str  # plain-text review report
    url: str  # reviewed offer URL

    # build from a stored or agent-returned dict
    @classmethod
    def from_dict(cls, d: dict) -> Verdict:
        return cls(d["tag"], d["content"], d["url"])


# One Profesia.sk offer
@dataclass
class Job:
    id: int  # Profesia offer ID, unique key for dedup
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
    uuid: str | None = None  # database primary key, set on insert
    verdict: Verdict | None = None

    # serialize to a plain dict
    def to_dict(self) -> dict:
        return asdict(self)

    # build from a plain dict, restoring nested salary and verdict
    @classmethod
    def from_dict(cls, d: dict) -> Job:
        d = dict(d)
        if d.get("salary") is not None:
            d["salary"] = Salary.from_dict(d["salary"])
        if d.get("verdict") is not None:
            d["verdict"] = Verdict.from_dict(d["verdict"])
        return cls(**d)
