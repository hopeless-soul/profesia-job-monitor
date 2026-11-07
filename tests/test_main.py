from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace

import profesia_monitor.agents.llm as llm
import profesia_monitor.agents.orchestrator as orchestrator
import profesia_monitor.__main__ as main
from profesia_monitor.__main__ import build_review, on_verdict, run_once
from profesia_monitor.config import Config
from profesia_monitor.models import Job, Verdict
from profesia_monitor.providers.profesia_sk.errors import ProfesiaBlockedError, ProfesiaError, ProfesiaRateLimitedError
from profesia_monitor.settings import RetryPolicy
from profesia_monitor.providers.base import SourceProvider
from profesia_monitor.store import connect, get_job, upsert_jobs
from profesia_monitor.telegram import TelegramError

T = "2026-09-23T10:00:00+00:00"


# Source stand-in: returns its jobs; raises `error` for the first `fail_times` calls, then succeeds
class FakeProvider(SourceProvider):
    def __init__(self, name, jobs=(), error=None, fail_times=0, retry=RetryPolicy(attempts=3, delay=0)):
        self.name = name
        self.label = name
        self.jobs = list(jobs)
        self.error = error
        self.fail_times = fail_times
        self.retry = retry
        self.calls = 0

    def fetch(self, known):
        self.calls += 1
        if self.error and self.calls <= self.fail_times:
            raise self.error
        return list(self.jobs)


# Telegram stand-in that records edits; edits in `fail_chats` raise
class EditBot:
    def __init__(self, fail_chats=()):
        self.fail_chats = set(fail_chats)
        self.edits: list[tuple[int, int, str]] = []

    def edit(self, chat_id, message_id, text):
        if chat_id in self.fail_chats:
            raise TelegramError(400, "Bad Request: message to edit not found")
        self.edits.append((chat_id, message_id, text))


# store one offer and return it with its uuid
def stored_offer(db):
    with closing(connect(db)) as conn, conn:
        return upsert_jobs(conn, [Job(1, "Dev", "https://www.profesia.sk/praca/x/O1", source="profesia")], seen_at=T)[0]


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
    offer = stored_offer(db)
    bot = EditBot(fail_chats={20})
    verdict = Verdict("GO", "fits <well>", offer.url)

    on_verdict(db, bot, offer, {10: 100, 20: 200, 30: 300}, finished(verdict))

    with closing(connect(db)) as conn:
        assert get_job(conn, 1, "profesia").verdict == verdict
    assert [(chat, msg) for chat, msg, _ in bot.edits] == [(10, 100), (30, 300)]
    assert "🟢 GO" in bot.edits[0][2]
    assert "fits &lt;well&gt;" in bot.edits[0][2]


# a failed review is only logged: nothing saved, nothing edited
def test_on_verdict_failure_only_logs(tmp_path, caplog):
    db = tmp_path / "jobs.db"
    offer = stored_offer(db)
    bot = EditBot()

    on_verdict(db, bot, offer, {10: 100}, finished(error=RuntimeError("LLM down")))

    with closing(connect(db)) as conn:
        assert get_job(conn, 1, "profesia").verdict is None
    assert bot.edits == []
    assert "verdict failed for offer 1" in caplog.text


# pet projects next to the backstory are appended to the text the agents get
def test_build_review_appends_pet_projects(tmp_path, monkeypatch):
    (tmp_path / "backstory.md").write_text("WORK-EXPERIENCE", encoding="utf-8")
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "A.md").write_text("PROJECT-A", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(llm, "build_llm", lambda model: None)
    monkeypatch.setattr(orchestrator, "build_orchestrator", lambda _, text: seen.setdefault("text", text))

    assert build_review(Config(backstory=tmp_path / "backstory.md")) is not None
    assert seen["text"].startswith("WORK-EXPERIENCE")
    assert "## Pet project: A\n\nPROJECT-A" in seen["text"]


# jobs from every provider go through the same store, notify and review; a duplicate of a stored offer gets neither
def test_run_once_merges_provider_jobs_and_skips_duplicates(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    with closing(connect(db)) as conn, conn:
        upsert_jobs(conn, [Job(1, "Dev", "https://www.profesia.sk/praca/x/O1", company="ACME", source="profesia")], seen_at=T)
    other_jobs = [
        Job(7, "dev", "https://www.linkedin.com/jobs/view/7/", company="acme", source="linkedin"),
        Job(8, "QA", "https://www.linkedin.com/jobs/view/8/", company="ACME", source="linkedin"),
    ]
    monkeypatch.setattr(main, "notify", lambda bot, subs, jobs: (False, {j.uuid: {10: j.id} for j in jobs}))
    verdicts = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(main, "VERDICTS", verdicts)
    reviewed = []

    run_once(
        Config(db=db), EditBot(), SimpleNamespace(chats={10: None}),
        lambda url: reviewed.append(url) or Verdict("GO", "ok", url),
        providers=[FakeProvider("linkedin", other_jobs)],
    )
    verdicts.shutdown(wait=True)

    assert reviewed == ["https://www.linkedin.com/jobs/view/8/"]
    with closing(connect(db)) as conn:
        assert get_job(conn, 7, "linkedin") is not None  # stored, so it isn't fetched again
        assert get_job(conn, 8, "linkedin").verdict.tag == "GO"


# a source that fails every attempt is logged and skipped; the other sources still store and notify their jobs
def test_run_once_keeps_other_providers_when_one_fails(tmp_path, monkeypatch, caplog):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(main, "notify", lambda bot, subs, jobs: (False, {}))
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    failing = FakeProvider("profesia", error=ProfesiaError("site down"), fail_times=99)
    providers = [
        failing,
        FakeProvider("linkedin", [Job(7, "QA", "https://www.linkedin.com/jobs/view/7/", source="linkedin")]),
    ]

    run_once(Config(db=db), EditBot(), SimpleNamespace(chats={10: None}), None, providers=providers)

    with closing(connect(db)) as conn:
        assert get_job(conn, 7, "linkedin") is not None
    assert failing.calls == 3  # all attempts used
    assert "profesia skipped after 3 attempt(s) (ProfesiaError): site down" in caplog.text


# a source that fails once then recovers is retried, waiting the configured delay between attempts
def test_run_once_retries_a_failed_source(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    pauses = []
    monkeypatch.setattr(main.time, "sleep", pauses.append)
    monkeypatch.setattr(main, "notify", lambda bot, subs, jobs: (False, {}))
    provider = FakeProvider(
        "profesia",
        [Job(1, "Dev", "https://www.profesia.sk/praca/x/O1", source="profesia")],
        error=ProfesiaError("timeout"),
        fail_times=1,
        retry=RetryPolicy(attempts=3, delay=12),
    )

    run_once(Config(db=db), EditBot(), SimpleNamespace(chats={10: None}), None, providers=[provider])

    assert provider.calls == 2
    assert pauses == [12]
    with closing(connect(db)) as conn:
        assert get_job(conn, 1, "profesia") is not None


# a failure a retry cannot fix is tried once and not retried
def test_fetch_source_does_not_retry_a_blocked_source(caplog):
    provider = FakeProvider("profesia", error=ProfesiaBlockedError("HTTP 403"), fail_times=99)

    assert main.fetch_source(provider, lambda ids: set()) is None
    assert provider.calls == 1
    assert "not retrying" in caplog.text


# a rate limit waits the Retry-After the source asked for, capped at MAX_RETRY_WAIT
def test_fetch_source_waits_the_retry_after(monkeypatch):
    pauses = []
    monkeypatch.setattr(main.time, "sleep", pauses.append)
    provider = FakeProvider(
        "profesia", [Job(1, "Dev", "https://www.profesia.sk/praca/x/O1", source="profesia")],
        error=ProfesiaRateLimitedError("HTTP 429", retry_after=7), fail_times=1,
    )
    assert main.fetch_source(provider, lambda ids: set())
    assert pauses == [7]

    slow = FakeProvider("profesia", error=ProfesiaRateLimitedError("HTTP 429", retry_after=9999), fail_times=1)
    main.fetch_source(slow, lambda ids: set())
    assert pauses[-1] == main.MAX_RETRY_WAIT
