import json
from dataclasses import replace
from pathlib import Path

from profesia_monitor.models import Job, Verdict
from profesia_monitor.parser import parse_listing
from profesia_monitor.store import connect, count_jobs, get_job, insert_jobs, load_jobs, save_verdict, upsert_jobs

ROOT = Path(__file__).parents[1]

T1 = "2026-09-23T10:00:00+00:00"
T2 = "2026-09-23T14:00:00+00:00"


def job(id: int, title: str = "Dev") -> Job:
    return Job(id=id, title=title, url=f"https://www.profesia.sk/praca/x/O{id}", company="X")


# known offers are refreshed and keep uuid, first_seen and verdict; only new offers are returned
def test_upsert_keeps_unique_ids_first_seen_uuid_and_verdict(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    first = upsert_jobs(conn, [job(1, "Old title"), job(2)], seen_at=T1)
    verdict = Verdict("GO", "fits", first[0].url)
    save_verdict(conn, first[0].uuid, verdict)

    added = upsert_jobs(conn, [job(1, "New title"), job(3)], seen_at=T2)

    assert [j.id for j in added] == [3]
    assert added[0].uuid and added[0].first_seen == T2
    assert count_jobs(conn) == 3
    stored = get_job(conn, 1)
    assert stored.title == "New title"
    assert (stored.first_seen, stored.last_seen) == (T1, T2)
    assert stored.uuid == first[0].uuid
    assert stored.verdict == verdict


# the same offer matched by two searches is stored once
def test_upsert_drops_duplicates_within_one_scrape(tmp_path):
    conn = connect(tmp_path / "jobs.db")

    added = upsert_jobs(conn, [job(1), job(1, "Duplicate")], seen_at=T1)

    assert [j.id for j in added] == [1]
    assert count_jobs(conn) == 1


# a legacy jobs.json imports with its timestamps and fresh UUIDs, and re-importing adds nothing
def test_import_legacy_json_round_trips(tmp_path):
    html = (ROOT / "tests" / "fixtures" / "listing_page1.html").read_text(encoding="utf-8")
    jobs = [replace(j, first_seen=T1, last_seen=T2) for j in parse_listing(html)[0]]
    legacy = tmp_path / "jobs.json"
    rows = [{k: v for k, v in j.to_dict().items() if k not in ("uuid", "verdict")} for j in jobs]
    legacy.write_text(json.dumps({"schema_version": 1, "updated_at": T2, "jobs": rows}), encoding="utf-8")
    conn = connect(tmp_path / "jobs.db")

    assert insert_jobs(conn, load_jobs(legacy)) == len(jobs)
    assert insert_jobs(conn, load_jobs(legacy)) == 0

    for original in jobs:
        stored = get_job(conn, original.id)
        assert stored.uuid
        assert replace(stored, uuid=None) == original
