# Database file: the schema of every table, opening a connection, and migrations.
# Kept in one place so the whole database can be read at once; the other modules only query it.

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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

# Tables: one row per offer, the Telegram message each chat got for it, the Telegram subscribers
# and the next update offset (one row at most), and the lease of the monitor that runs on this database (one row at most)
_SCHEMA = _OFFERS_TABLE.format(name="offers") + """
CREATE TABLE IF NOT EXISTS offer_messages (
    offer_id TEXT NOT NULL REFERENCES offers(uuid),
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (offer_id, chat_id)
);
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id       INTEGER PRIMARY KEY,
    name          TEXT,
    subscribed_at TEXT
);
CREATE TABLE IF NOT EXISTS telegram_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    update_offset INTEGER NOT NULL            -- next getUpdates offset, so each command is handled once
);
CREATE TABLE IF NOT EXISTS monitor_lease (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    owner     TEXT NOT NULL,                  -- hostname:pid of the running monitor
    heartbeat TEXT NOT NULL                   -- ISO UTC time the owner last renewed it
);
"""


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


# Bring older databases up to the current schema; every step checks first, so it runs once.
# Offers made before the LinkedIn source have UNIQUE(id) and no source column:
# rebuild the table once (SQLite can't change a constraint in place), marking old rows as Profesia
def _migrate(conn: sqlite3.Connection) -> None:
    # offer_messages made before its key was renamed to offer_id: rename the column in place (keeps the rows)
    message_columns = [row[1] for row in conn.execute("PRAGMA table_info(offer_messages)")]
    if "offer_uuid" in message_columns:
        conn.execute("ALTER TABLE offer_messages RENAME COLUMN offer_uuid TO offer_id")

    # offers made before the LinkedIn source
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
