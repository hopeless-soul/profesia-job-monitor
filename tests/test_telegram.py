import pytest
import requests

from profesia_monitor.models import Job, Verdict
from profesia_monitor.telegram import (
    MAX_MESSAGE_LEN,
    Bot,
    Subscribers,
    TelegramAuthError,
    TelegramBlockedError,
    TelegramError,
    TelegramNetworkError,
    TelegramRateLimitedError,
    _telegram_error,
    format_verdict,
    handle_updates,
    notify,
)


# Telegram stand-in that records sends and edits; chats in `blocked` refuse messages
class FakeBot:
    def __init__(self, updates=(), blocked=()):
        self.updates = list(updates)
        self.blocked = set(blocked)
        self.sent: list[tuple[int, str]] = []
        self.edits: list[tuple[int, int, str]] = []

    def get_updates(self, offset, timeout=0):
        return [u for u in self.updates if u["update_id"] >= offset]

    def send(self, chat_id, text):
        if chat_id in self.blocked:
            raise TelegramBlockedError(403, "Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text))
        return len(self.sent)  # message_id

    def edit(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))


# an update as Telegram sends it for a text message
def message(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "username": f"u{chat_id}"}, "text": text}}


# a stored Profesia offer for the message tests
def job(i, title="Frontend developer"):
    return Job(
        id=i, title=title, url=f"https://www.profesia.sk/praca/x/O{i}", company="ACME <s.r.o.>",
        uuid=f"u{i}", source="profesia",
    )


# /start and /stop update subscribers, and each update is handled once
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


# a verdict message shows the tag, escapes HTML and is cut to Telegram's limit
def test_verdict_message_fits_telegram_limit_and_escapes_html():
    offer = job(1, "x" * 300)
    offer.verdict = Verdict("STRETCH", "<b>gap</b> & more " * 1000, offer.url)

    text = format_verdict(offer)

    assert len(text) <= MAX_MESSAGE_LEN
    assert "ACME &lt;s.r.o.&gt;" in text
    assert "STRETCH</b>" in text
    assert "&lt;b&gt;gap&lt;/b&gt; &amp; more" in text
    assert text.endswith("…</blockquote>")


# each offer is its own message per chat, and the returned ids say which message each chat got
def test_notify_sends_one_message_per_offer_and_returns_ids(tmp_path, monkeypatch):
    monkeypatch.setattr("profesia_monitor.telegram.time.sleep", lambda s: None)
    subs = Subscribers.load(tmp_path / "subscribers.json")
    bot = FakeBot([message(1, 1, "/start"), message(2, 2, "/start")])
    handle_updates(bot, subs)
    bot.sent.clear()

    changed, ids = notify(bot, subs, [job(1), job(2)])

    assert not changed
    assert len(bot.sent) == 4
    assert ids == {"u1": {1: 1, 2: 3}, "u2": {1: 2, 2: 4}}


# Response stand-in with a fixed JSON body
class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


# Session stand-in whose first `fail_times` posts time out, then succeed
class FlakySession:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise requests.exceptions.ReadTimeout("timed out")
        return FakeResponse({"ok": True, "result": []})


# a single network timeout is retried
def test_call_retries_once_after_a_network_timeout():
    session = FlakySession(fail_times=1)
    bot = Bot("token", session=session)

    assert bot.get_updates(0, timeout=30) == []
    assert session.calls == 2


# a second timeout is raised
def test_call_gives_up_after_two_network_timeouts():
    session = FlakySession(fail_times=2)
    bot = Bot("token", session=session)

    with pytest.raises(TelegramNetworkError) as info:
        bot.get_updates(0, timeout=30)
    assert session.calls == 2
    assert isinstance(info.value.__cause__, requests.exceptions.ReadTimeout)


# chats that blocked the bot are unsubscribed
def test_notify_drops_chats_that_blocked_the_bot(tmp_path):
    subs = Subscribers.load(tmp_path / "subscribers.json")
    bot = FakeBot([message(1, 1, "/start"), message(2, 2, "/start")], blocked={2})
    handle_updates(bot, subs)
    bot.sent.clear()

    changed, ids = notify(bot, subs, [job(1)])
    assert changed
    assert ids == {"u1": {1: 1}}
    assert list(subs.chats) == [1]
    assert [chat for chat, _ in bot.sent] == [1]


# the details line names the offer's source, using the provider's label
def test_format_shows_source():
    offer = job(1)
    assert "ACME &lt;s.r.o.&gt; · Profesia" in format_verdict(offer)
    offer.source = "linkedin"
    assert "· LinkedIn" in format_verdict(offer)


# the error reply's code and text pick the typed error: token, blocked chat, rate limit, anything else
def test_error_reply_maps_to_its_class():
    assert isinstance(_telegram_error(401, "Unauthorized", None), TelegramAuthError)
    assert isinstance(_telegram_error(403, "Forbidden", None), TelegramBlockedError)
    assert isinstance(_telegram_error(400, "Bad Request: chat not found", None), TelegramBlockedError)
    rate = _telegram_error(429, "Too Many Requests", 5)
    assert isinstance(rate, TelegramRateLimitedError) and rate.retry_after == 5
    assert type(_telegram_error(500, "boom", None)) is TelegramError
