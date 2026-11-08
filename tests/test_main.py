from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace

import pytest

import offer_monitor.__main__ as main
import offer_monitor.agents.llm as llm
import offer_monitor.agents.orchestrator as orchestrator
from offer_monitor.__main__ import build_review, fetch_source, on_verdict, run
from offer_monitor.config import Config, ModelConfig
from offer_monitor.models import Job, Verdict
from offer_monitor.providers.base import SourceProvider
from offer_monitor.providers.profesia_sk.errors import ProfesiaBlockedError, ProfesiaError, ProfesiaRateLimitedError
from offer_monitor.settings import RetryPolicy
from offer_monitor.store import connect, count_jobs, get_job, store_new
from offer_monitor.telegram import TelegramError

T = "2026-09-23T10:00:00+00:00"


# an offer with a URL built from its source and id
def offer(id=1, title="Dev", company=None, source="profesia"):
    return Job(id, title, f"https://{source}.example/{id}", company=company, source=source)


# Source stand-in: returns its jobs; raises `error` for the first `fail_times` calls, then succeeds
class FakeProvider(SourceProvider):
    def __init__(self, name, jobs=(), error=None, fail_times=0, retry=RetryPolicy(attempts=3, delay=0)):
        self.name, self.jobs, self.error, self.fail_times, self.retry = name, list(jobs), error, fail_times, retry
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return list(self.jobs)


# Telegram stand-in that records edits; edits in `fail_chats` raise
class EditBot:
    def __init__(self, fail_chats=()):
        self.fail_chats = set(fail_chats)
        self.edits = []

    def edit(self, chat_id, message_id, text):
        if chat_id in self.fail_chats:
            raise TelegramError(400, "Bad Request: message to edit not found")
        self.edits.append((chat_id, message_id, text))


# run_once's verdict pool, swapped for one the test can wait on
@pytest.fixture
def verdicts(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(main, "VERDICTS", pool)
    return pool


# store one offer and return it with its uuid
def stored_offer(db):
    with closing(connect(db)) as conn, conn:
        return store_new(conn, [offer()], seen_at=T)[0]


# a future that already finished with a result or an error
def finished(result=None, error=None) -> Future:
    future = Future()
    if error:
        future.set_exception(error)
    else:
        future.set_result(result)
    return future


# a verdict is saved and added to every chat's message; one failed edit doesn't stop the rest
def test_on_verdict_saves_and_edits_every_message(tmp_path):
    db = tmp_path / "jobs.db"
    job = stored_offer(db)
    bot = EditBot(fail_chats={20})
    verdict = Verdict("GO", "fits <well>", job.url)

    on_verdict(db, bot, job, {10: 100, 20: 200, 30: 300}, finished(verdict))

    with closing(connect(db)) as conn:
        assert get_job(conn, 1, "profesia").verdict == verdict
    assert [(chat, msg) for chat, msg, _ in bot.edits] == [(10, 100), (30, 300)]
    assert "🟢 GO" in bot.edits[0][2]
    assert "fits &lt;well&gt;" in bot.edits[0][2]


# a failed review is only logged: nothing saved, nothing edited
def test_on_verdict_failure_only_logs(tmp_path, caplog):
    db = tmp_path / "jobs.db"
    job = stored_offer(db)
    bot = EditBot()

    on_verdict(db, bot, job, {10: 100}, finished(error=RuntimeError("LLM down")))

    with closing(connect(db)) as conn:
        assert get_job(conn, 1, "profesia").verdict is None
    assert bot.edits == []
    assert "verdict failed for offer 1" in caplog.text


# no API key in config or env: verdicts are disabled with a warning, the monitor keeps going
def test_build_review_without_api_key_disables_verdicts(tmp_path, monkeypatch, caplog):
    (tmp_path / "backstory.md").write_text("WORK-EXPERIENCE", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert build_review(Config(backstory=tmp_path / "backstory.md", model=ModelConfig(provider="anthropic"))) is None
    assert "verdicts disabled: model.api_key: not set in config.yaml and ANTHROPIC_API_KEY is unset" in caplog.text


# pet projects next to the backstory are appended to the text the agents get
def test_build_review_appends_pet_projects(tmp_path, monkeypatch):
    (tmp_path / "backstory.md").write_text("WORK-EXPERIENCE", encoding="utf-8")
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "A.md").write_text("PROJECT-A", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(llm, "build_llm", lambda model: None)
    monkeypatch.setattr(orchestrator, "build_orchestrator", lambda _, text: seen.setdefault("text", text))

    assert build_review(Config(backstory=tmp_path / "backstory.md", model=ModelConfig(provider="anthropic"))) is not None
    assert seen["text"].startswith("WORK-EXPERIENCE")
    assert "## Pet project: A\n\nPROJECT-A" in seen["text"]


# new jobs are stored, notified and reviewed, and the verdict edits the notified message;
# a title + company duplicate of a stored offer is stored but gets neither
def test_run_once_notifies_and_reviews_only_fresh_offers(tmp_path, monkeypatch, verdicts):
    db = tmp_path / "jobs.db"
    with closing(connect(db)) as conn, conn:
        store_new(conn, [offer(1, company="ACME")], seen_at=T)
    monkeypatch.setattr(main, "notify", lambda bot, subs, jobs: (False, {j.uuid: {10: j.id} for j in jobs}))
    bot = EditBot()
    reviewed = []

    run(
        Config(db=db), bot, SimpleNamespace(chats={10: None}, active_ids=lambda: [10]),
        lambda url: reviewed.append(url) or Verdict("GO", "ok", url),
        providers=[FakeProvider("linkedin", [offer(7, "dev", "acme", "linkedin"), offer(8, "QA", "ACME", "linkedin")])],
    )
    verdicts.shutdown(wait=True)

    assert reviewed == [offer(8, source="linkedin").url]
    assert [(chat, msg) for chat, msg, _ in bot.edits] == [(10, 8)]
    with closing(connect(db)) as conn:
        assert get_job(conn, 7, "linkedin") is not None  # stored, so it isn't fetched again
        assert get_job(conn, 8, "linkedin").verdict.tag == "GO"


# a repeated id is stored and reviewed once; a second run on the same DB finds nothing new
def test_run_once_dedups_fetched_then_db(tmp_path, verdicts):
    db = tmp_path / "jobs.db"
    reviewed = []
    provider = FakeProvider("profesia", [offer(), offer(title="Dev again")])
    review = lambda url: reviewed.append(url) or Verdict("GO", "ok", url)

    run(Config(db=db), None, None, review, providers=[provider])
    run(Config(db=db), None, None, review, providers=[provider])
    verdicts.shutdown(wait=True)

    assert reviewed == [offer().url]
    with closing(connect(db)) as conn:
        assert count_jobs(conn) == 1
        assert get_job(conn, 1, "profesia").title == "Dev"


# run_once uses the provider's own db_dedup
def test_run_once_calls_overridden_db_dedup(tmp_path):
    class NothingNew(FakeProvider):
        def db_dedup(self, jobs, known):
            return []

    db = tmp_path / "jobs.db"
    run(Config(db=db), None, None, None, providers=[NothingNew("profesia", [offer()])])

    with closing(connect(db)) as conn:
        assert count_jobs(conn) == 0


# a source that fails every attempt is logged and skipped; the other sources are still stored
def test_run_once_keeps_other_providers_when_one_fails(tmp_path, caplog):
    db = tmp_path / "jobs.db"
    failing = FakeProvider("profesia", error=ProfesiaError("site down"), fail_times=99)

    run(Config(db=db), None, None, None, providers=[failing, FakeProvider("linkedin", [offer(7, source="linkedin")])])

    with closing(connect(db)) as conn:
        assert get_job(conn, 7, "linkedin") is not None
    assert failing.calls == 3  # all attempts used
    assert "profesia skipped after 3 attempt(s) (ProfesiaError): site down" in caplog.text


# a source that fails once is retried after the policy's delay, or after the Retry-After it sent
@pytest.mark.parametrize("error, pause", [
    (ProfesiaError("timeout"), 12),
    (ProfesiaRateLimitedError("HTTP 429", retry_after=7), 7),
])
def test_fetch_source_retries_after_a_pause(monkeypatch, error, pause):
    pauses = []
    monkeypatch.setattr(main.time, "sleep", pauses.append)
    provider = FakeProvider("profesia", [offer()], error=error, fail_times=1, retry=RetryPolicy(attempts=3, delay=12))

    assert fetch_source(provider) == [offer()]
    assert (provider.calls, pauses) == (2, [pause])


# a failure a retry cannot fix is tried once and not retried
def test_fetch_source_does_not_retry_a_blocked_source(caplog):
    provider = FakeProvider("profesia", error=ProfesiaBlockedError("HTTP 403"), fail_times=99)

    assert fetch_source(provider) is None
    assert provider.calls == 1
    assert "not retrying" in caplog.text
