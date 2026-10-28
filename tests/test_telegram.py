import pytest
import requests

from profesia_monitor.models import Job
from profesia_monitor.telegram import (
    MAX_MESSAGE_LEN,
    Bot,
    Subscribers,
    TelegramError,
    build_messages,
    handle_updates,
    notify,
)


class FakeBot:
    def __init__(self, updates=(), blocked=()):
        self.updates = list(updates)
        self.blocked = set(blocked)
        self.sent: list[tuple[int, str]] = []

    def get_updates(self, offset, timeout=0):
        return [u for u in self.updates if u["update_id"] >= offset]

    def send(self, chat_id, text):
        if chat_id in self.blocked:
            raise TelegramError(403, "Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text))


def message(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "username": f"u{chat_id}"}, "text": text}}


def job(i, title="Frontend developer"):
    return Job(id=i, title=title, url=f"https://www.profesia.sk/praca/x/O{i}", company="ACME <s.r.o.>")


def test_start_and_stop_update_subscribers_once(tmp_path):
    path = tmp_path / "subscribers.json"
    subs = Subscribers.load(path)
    bot = FakeBot([message(10, 1, "/start"), message(11, 2, "/start@ProfesiaBot"), message(12, 1, "/stop")])

    assert handle_updates(bot, subs)
    subs.save()

    reloaded = Subscribers.load(path)
    assert list(reloaded.chats) == [2]
    assert reloaded.offset == 13
    assert len(bot.sent) == 3  # every command gets a reply
    assert not handle_updates(bot, reloaded)  # already-handled updates are not replayed


def test_messages_fit_telegram_limit_and_escape_html():
    messages = build_messages([job(i, "x" * 300) for i in range(50)])

    assert len(messages) > 1
    assert all(len(m) <= MAX_MESSAGE_LEN for m in messages)
    assert "ACME &lt;s.r.o.&gt;" in messages[0]
    assert sum(m.count("<a href") for m in messages) == 50


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FlakySession:
    # Raises a network timeout on the first `fail_times` calls, then succeeds.

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise requests.exceptions.ReadTimeout("timed out")
        return FakeResponse({"ok": True, "result": []})


def test_call_retries_once_after_a_network_timeout():
    session = FlakySession(fail_times=1)
    bot = Bot("token", session=session)

    assert bot.get_updates(0, timeout=30) == []
    assert session.calls == 2


def test_call_gives_up_after_two_network_timeouts():
    session = FlakySession(fail_times=2)
    bot = Bot("token", session=session)

    with pytest.raises(requests.exceptions.ReadTimeout):
        bot.get_updates(0, timeout=30)
    assert session.calls == 2


def test_notify_drops_chats_that_blocked_the_bot(tmp_path):
    subs = Subscribers.load(tmp_path / "subscribers.json")
    bot = FakeBot([message(1, 1, "/start"), message(2, 2, "/start")], blocked={2})
    handle_updates(bot, subs)
    bot.sent.clear()

    assert notify(bot, subs, [job(1)])
    assert list(subs.chats) == [1]
    assert [chat for chat, _ in bot.sent] == [1]
