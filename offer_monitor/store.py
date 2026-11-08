# Job store: SQLite database, offers unique by (source, id).
# Callers own transactions: wrap writes in `with conn:` to commit.

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Job, Verdict

# Offers table; {name} lets the migration build it under a temporary name
_OFFERS_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    uuid            TEXT PRIMARY KEY,
    source          TEXT NOT NULL,            -- provider name, set on every insert
    id              INTEGER NOT NULL,         -- offer ID on its source site
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    company         TEXT,
    company_id      INTEGER,
    location        TEXT,
    salary          TEXT,                     -- JSON object or NULL
    labels          TEXT NOT NULL,            -- JSON list
    posted_text     TEXT,
    first_seen      TEXT,
    last_seen       TEXT,
    verdict_tag     TEXT,
    verdict_content TEXT,
    verdict_url     TEXT,
    UNIQUE (source, id)
);
"""

# Tables: one row per offer, plus the Telegram message each chat got for it
_SCHEMA = _OFFERS_TABLE.format(name="offers") + """
CREATE TABLE IF NOT EXISTS offer_messages (
    offer_id TEXT NOT NULL REFERENCES offers(uuid),
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (offer_id, chat_id)
);
"""

# Columns written on insert (verdict columns are filled later by save_verdict)
_INSERT_COLUMNS = (
    "uuid",
    "source",
    "id",
    "title",
    "url",
    "company",
    "company_id",
    "location",
    "salary",
    "labels",
    "posted_text",
    "first_seen",
    "last_seen",
)

# Columns refreshed every time a known offer is scraped again
_REFRESH_COLUMNS = (
    "title",
    "url",
    "company",
    "company_id",
    "location",
    "salary",
    "labels",
    "posted_text",
    "last_seen",
)


# Current UTC time as an ISO string
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Open the database, creating the file and tables when missing
def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


# Databases made before the LinkedIn source have UNIQUE(id) and no source column:
# rebuild the table once (SQLite can't change a constraint in place), marking old rows as Profesia
def _migrate(conn: sqlite3.Connection) -> None:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(offers)")]
    if "source" in columns:
        return
    names = ", ".join(columns)
    conn.executescript(
        "BEGIN;"
        + _OFFERS_TABLE.format(name="offers_new")
        + f"INSERT INTO offers_new ({names}, source) SELECT {names}, 'profesia' FROM offers;"
        "DROP TABLE offers;"
        "ALTER TABLE offers_new RENAME TO offers;"
        "COMMIT;"
    )


# Job -> column values, with salary and labels encoded as JSON
def _row(job: Job) -> dict:
    row = job.to_dict()
    del row["verdict"]
    row["salary"] = json.dumps(row["salary"], ensure_ascii=False) if row["salary"] else None
    row["labels"] = json.dumps(row["labels"], ensure_ascii=False)
    return row


### Returns: Duplicate key; normalized title + company;
def _dup_key(title: str, company: str | None) -> tuple[str, str] | None:
    if not company:
        return None
    return " ".join(title.split()).casefold(), " ".join(company.split()).casefold()


### Insert offers whose (source, id) is not stored yet
def insert_jobs(conn: sqlite3.Connection, jobs: list[Job]) -> None:
    # Generate job database key
    for job in jobs:
        job.uuid = job.uuid or str(uuid.uuid4())

    # insert; OR IGNORE only guards against a second run storing the same offer meanwhile
    conn.executemany(
        f"INSERT OR IGNORE INTO offers ({', '.join(_INSERT_COLUMNS)}) "
        f"VALUES ({', '.join(':' + c for c in _INSERT_COLUMNS)})",
        [_row(job) for job in jobs],
    )


# Refresh offers that are already stored, keeping uuid, first_seen and verdict; jobs not stored are untouched
def refresh_known(conn: sqlite3.Connection, jobs: list[Job], seen_at: str) -> None:
    conn.executemany(
        f"UPDATE offers SET {', '.join(f'{c} = :{c}' for c in _REFRESH_COLUMNS)} "
        "WHERE source = :source AND id = :id",
        [_row(job) | {"last_seen": seen_at} for job in jobs],
    )


### Saves new offers; returns the ones to notify and review.
### same `title` + `company` (any source) dedupes in the result but still is saved.
### batch store is an option.
def store_new(conn: sqlite3.Connection, new: list[Job], seen_at: str) -> list[Job]:
    if not new:
        return []
    taken = {_dup_key(title, company) for title, company in conn.execute("SELECT title, company FROM offers")}
    fresh = []
    for job in new:
        job.first_seen = job.last_seen = seen_at
        key = _dup_key(job.title, job.company)
        if key is None or key not in taken:
            fresh.append(job)
            taken.add(key)

    insert_jobs(conn, new)
    return fresh


### Checks for known `id`s from the specific `source`. Returns known list;
def known_ids(conn: sqlite3.Connection, source: str, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    id_placeholders = ", ".join("?" for _ in ids)
    query = f"SELECT id FROM offers WHERE source = ? AND id IN ({id_placeholders})"

    rows = conn.execute(query, [source, *ids])
    return {row[0] for row in rows}


### Number of stored offers;
def count_jobs(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]


### Remember the Offer Telegram `chat_id` & `msg_id` pairs;
def save_message_ids(conn: sqlite3.Connection, offer_id: str, message_ids: dict[int, int]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO offer_messages (offer_id, chat_id, message_id) VALUES (?, ?, ?)",
        [(offer_id, chat_id, message_id) for chat_id, message_id in message_ids.items()],
    )


### Attach agent's verdict to offer;
def save_verdict(conn: sqlite3.Connection, offer_id: str, verdict: Verdict) -> None:
    conn.execute(
        "UPDATE offers SET verdict_tag = ?, verdict_content = ?, verdict_url = ? WHERE uuid = ?",
        (verdict.tag, verdict.content, verdict.url, offer_id),
    )


### Get offer by its `source` & `id`;
def get_job(conn: sqlite3.Connection, id: int, source: str) -> Job | None:
    row = conn.execute("SELECT * FROM offers WHERE source = ? AND id = ?", (source, id)).fetchone()
    if row is None:
        return None

    # decode JSON columns and regroup the verdict columns
    d = dict(row)
    tag, content, url = d.pop("verdict_tag"), d.pop("verdict_content"), d.pop("verdict_url")
    d["salary"] = json.loads(d["salary"]) if d["salary"] else None
    d["labels"] = json.loads(d["labels"])
    d["verdict"] = {"tag": tag, "content": content, "url": url} if tag is not None else None
    return Job.from_dict(d)
