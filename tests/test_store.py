import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from offer_monitor.models import Job, Verdict
from offer_monitor.providers.profesia_sk.parser import parse_listing
from offer_monitor.store import (
    connect,
    count_jobs,
    get_job,
    insert_jobs,
    known_ids,
    load_jobs,
    refresh_known,
    save_verdict,
    store_new,
)

PAGE = (Path(__file__).parent / "fixtures" / "listing_page1.html").read_text(encoding="utf-8")

T1 = "2026-09-23T10:00:00+00:00"
T2 = "2026-09-23T14:00:00+00:00"


# an offer with a default title and company
def job(id: int, title: str | None = None, company: str | None = "X", source: str = "profesia") -> Job:
    return Job(id=id, title=title or f"Dev {id}", url=f"https://example.com/{id}", company=company, source=source)


# refreshed offers get new fields and last_seen, and keep uuid, first_seen and verdict; unstored ones are untouched
def test_refresh_known_keeps_first_seen_uuid_and_verdict(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    first = store_new(conn, [job(1, "Old title")], seen_at=T1)
    verdict = Verdict("GO", "fits", first[0].url)
    save_verdict(conn, first[0].uuid, verdict)

    refresh_known(conn, [job(1, "New title"), job(3)], seen_at=T2)

    assert count_jobs(conn) == 1
    stored = get_job(conn, 1, "profesia")
    assert (stored.title, stored.first_seen, stored.last_seen) == ("New title", T1, T2)
    assert stored.uuid == first[0].uuid
    assert stored.verdict == verdict


# new offers get a uuid and first_seen
def test_store_new_sets_uuid_and_first_seen(tmp_path):
    conn = connect(tmp_path / "jobs.db")

    added = store_new(conn, [job(1), job(2)], seen_at=T1)

    assert [j.id for j in added] == [1, 2]
    assert all(j.uuid and j.first_seen == j.last_seen == T1 for j in added)
    assert count_jobs(conn) == 2


# a legacy jobs.json (no source field back then) imports as Profesia offers with their timestamps
# and fresh UUIDs, and re-importing adds nothing
def test_import_legacy_json_round_trips(tmp_path):
    jobs = [replace(j, first_seen=T1, last_seen=T2) for j in parse_listing(PAGE, "profesia")[0]]
    rows = [{k: v for k, v in j.to_dict().items() if k not in ("uuid", "verdict", "source")} for j in jobs]
    legacy = tmp_path / "jobs.json"
    legacy.write_text(json.dumps({"schema_version": 1, "updated_at": T2, "jobs": rows}), encoding="utf-8")
    conn = connect(tmp_path / "jobs.db")

    assert insert_jobs(conn, load_jobs(legacy)) == len(jobs)
    assert insert_jobs(conn, load_jobs(legacy)) == 0
    for original in jobs:
        stored = get_job(conn, original.id, "profesia")
        assert stored.uuid
        assert replace(stored, uuid=None) == original


# the same ID on two sources is two offers
def test_same_id_on_two_sources_is_two_offers(tmp_path):
    conn = connect(tmp_path / "jobs.db")

    added = store_new(conn, [job(1, "A"), job(1, "B", source="linkedin")], seen_at=T1)

    assert len(added) == 2
    assert get_job(conn, 1, "profesia").title == "A"
    assert get_job(conn, 1, "linkedin").title == "B"
    assert known_ids(conn, "linkedin", [1, 2]) == {1}


# a new offer with a stored title + company (any source) or one earlier in the batch is stored but not returned
def test_duplicate_title_and_company_is_stored_but_not_returned(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    store_new(conn, [job(1, "C#  Developer", "Siemens")], seen_at=T1)

    added = store_new(conn, [
        job(7, "c# developer", "SIEMENS", source="linkedin"),  # same as stored, other source
        job(8, "Java dev", "ACME", source="linkedin"),
        job(9, "Java Dev", "acme", source="linkedin"),  # same as an earlier one in this scrape
    ], seen_at=T2)

    assert [j.id for j in added] == [8]
    assert count_jobs(conn) == 4
    assert known_ids(conn, "linkedin", [7, 8, 9]) == {7, 8, 9}


# offers without a company are never duplicates
def test_offers_without_company_are_never_duplicates(tmp_path):
    conn = connect(tmp_path / "jobs.db")

    added = store_new(conn, [job(1, "Dev", None), job(2, "Dev", None)], seen_at=T1)

    assert [j.id for j in added] == [1, 2]


# a database from before the LinkedIn source keeps its rows and messages, now marked as Profesia
def test_old_database_is_migrated(tmp_path):
    db = tmp_path / "jobs.db"
    old = sqlite3.connect(db)
    old.executescript("""
        CREATE TABLE offers (
            uuid TEXT PRIMARY KEY, id INTEGER NOT NULL UNIQUE, title TEXT NOT NULL, url TEXT NOT NULL,
            company TEXT, company_id INTEGER, location TEXT, salary TEXT, labels TEXT NOT NULL,
            posted_text TEXT, first_seen TEXT, last_seen TEXT,
            verdict_tag TEXT, verdict_content TEXT, verdict_url TEXT
        );
        CREATE TABLE offer_messages (
            offer_uuid TEXT NOT NULL REFERENCES offers(uuid), chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL, PRIMARY KEY (offer_uuid, chat_id)
        );
        INSERT INTO offers (uuid, id, title, url, labels, verdict_tag, verdict_content, verdict_url)
            VALUES ('u1', 1, 'Dev', 'https://example.com/1', '[]', 'GO', 'fits', 'https://example.com/1');
        INSERT INTO offer_messages VALUES ('u1', 10, 100);
    """)
    old.close()

    conn = connect(db)

    stored = get_job(conn, 1, "profesia")
    assert (stored.uuid, stored.source, stored.verdict.tag) == ("u1", "profesia", "GO")
    assert [tuple(r) for r in conn.execute("SELECT * FROM offer_messages")] == [("u1", 10, 100)]
    # the new key allows the same ID on another source, and the messages table still links to offers
    store_new(conn, [job(1, source="linkedin")], seen_at=T1)
    assert "REFERENCES offers(" in conn.execute("SELECT sql FROM sqlite_master WHERE name = 'offer_messages'").fetchone()[0]
    connect(db).close()  # second connect doesn't migrate again
