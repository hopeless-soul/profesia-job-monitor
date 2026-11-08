# Job store: one SQLite database, offers unique by (source, id).
# Callers own transactions: wrap writes in `with conn:` to commit.
#
#   db.py           schema of every table, connect (creates and migrates them), now_iso
#   jobs.py         offers: insert, refresh, dedup, verdicts
#   subscribers.py  Telegram: subscribers, update offset, the message each chat got per offer
#   lease.py        the one-monitor-per-database lease
#
# Everything is re-exported here, so callers keep importing from `offer_monitor.store`.

from .db import connect, now_iso
from .jobs import count_jobs, get_job, insert_jobs, known_ids, refresh_known, save_verdict, store_new
from .lease import LEASE_BEAT, LEASE_TTL, claim_lease, release_lease, renew_lease
from .subscribers import load_subscribers, save_message_ids, save_subscribers
