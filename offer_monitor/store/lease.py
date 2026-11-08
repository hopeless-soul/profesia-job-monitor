# Monitor lease: one row in monitor_lease says which monitor owns the database,
# so only one runs per database wherever it runs (Windows or the Docker container).

from __future__ import annotations

import sqlite3
from datetime import datetime

# A lease whose heartbeat is older than this is from a stopped monitor and can be taken over
LEASE_TTL = 120  # seconds
# How often the running monitor renews its lease; well under LEASE_TTL
LEASE_BEAT = 30  # seconds


### Assigns database to `owner`; Returns None when taken.
### The same owner (e.g. a restarted container: same hostname, PID 1) takes its own lease back right away.
def claim_lease(conn: sqlite3.Connection, owner: str, now: str) -> str | None:
    # take the write lock before reading, so two monitors starting together can't both see a free lease
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT owner, heartbeat FROM monitor_lease").fetchone()

    # refuse while another monitor's heartbeat is fresh
    if row and row["owner"] != owner:
        age = datetime.fromisoformat(now) - datetime.fromisoformat(row["heartbeat"])
        if age.total_seconds() < LEASE_TTL:
            return row["owner"]

    # free, stale or ours: take it
    conn.execute("INSERT OR REPLACE INTO monitor_lease (id, owner, heartbeat) VALUES (1, ?, ?)", (owner, now))
    return None


### Refresh `owner`'s heartbeat; False when the lease is no longer ours (another monitor took it over)
def renew_lease(conn: sqlite3.Connection, owner: str, now: str) -> bool:
    return conn.execute("UPDATE monitor_lease SET heartbeat = ? WHERE owner = ?", (now, owner)).rowcount == 1


### Free the database, only if `owner` still holds it
def release_lease(conn: sqlite3.Connection, owner: str) -> None:
    conn.execute("DELETE FROM monitor_lease WHERE owner = ?", (owner,))
