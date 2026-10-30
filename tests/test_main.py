from concurrent.futures import Future
from contextlib import closing

import profesia_monitor.agents.llm as llm
import profesia_monitor.agents.orchestrator as orchestrator
from profesia_monitor.__main__ import build_review, on_verdict
from profesia_monitor.config import Config
from profesia_monitor.models import Job, Verdict
from profesia_monitor.store import connect, get_job, upsert_jobs
from profesia_monitor.telegram import TelegramError

T = "2026-09-23T10:00:00+00:00"


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
        return upsert_jobs(conn, [Job(1, "Dev", "https://www.profesia.sk/praca/x/O1")], seen_at=T)[0]


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
        assert get_job(conn, 1).verdict == verdict
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
        assert get_job(conn, 1).verdict is None
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
