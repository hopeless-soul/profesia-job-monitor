# JSON job store, unique by offer ID. Format: schemas/jobs.schema.json.

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import Job

SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_jobs(path: Path) -> list[Job]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"{path}: unsupported schema_version {version!r}")
    return [Job.from_dict(d) for d in data["jobs"]]


def merge_jobs(
    existing: list[Job], scraped: list[Job], seen_at: str | None = None
) -> tuple[list[Job], list[Job]]:
    # Merge scraped jobs into existing ones by `id`.
    #
    # Known jobs get fresh scraped fields and `last_seen`, keeping `first_seen`.
    # Returns (all jobs, newly added jobs).
    seen_at = seen_at or now_iso()
    by_id = {job.id: job for job in existing}
    added: list[Job] = []

    for job in scraped:
        old = by_id.get(job.id)
        job.first_seen = old.first_seen if old else seen_at
        job.last_seen = seen_at
        if not old:
            added.append(job)
        by_id[job.id] = job

    merged = sorted(by_id.values(), key=lambda j: (j.first_seen or "", j.id), reverse=True)
    return merged, added


def save_jobs(path: Path, jobs: list[Job]) -> None:
    # Write atomically so a crash never leaves a truncated file.
    data = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now_iso(),
        "jobs": [job.to_dict() for job in jobs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
