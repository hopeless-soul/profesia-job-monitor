from types import SimpleNamespace

import pytest
import requests

from offer_monitor.models import Job, Verdict
from offer_monitor.telegram import (
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


# Telegram stand-in that records sends; chats in `blocked` refuse messages
class FakeBot:
    def __init__(self, updates=(), blocked=()):
        self.updates = list(updates)
        self.blocked = set(blocked)
        self.sent = []

    def get_updates(self, offset, timeout=0):
        return [u for u in self.updates if u["update_id"] >= offset]

    def send(self, chat_id, text):
        if chat_id in self.blocked:
            raise TelegramBlockedError(403, "Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text))
        return len(self.sent)  # message_id


# Session stand-in whose first `fail_times` posts time out, then succeed
class FlakySession:
    def __init__(self, fail_times, message="timed out"):
        self.fail_times = fail_times
        self.message = message
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise requests.exceptions.ReadTimeout(self.message)
        return SimpleNamespace(json=lambda: {"ok": True, "result": []})


# an update as Telegram sends it for a text message
def message(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "username": f"u{chat_id}"}, "text": text}}


# a stored Profesia offer for the message tests
def job(i, title="Frontend developer"):
    return Job(
        id=i, title=title, url=f"https://www.profesia.sk/praca/x/O{i}", company="ACME <s.r.o.>",
        uuid=f"u{i}", source="profesia",
    )


# subscribers for chats 1 and 2, made through /start like real ones
def two_subscribers(bot, tmp_path):
    subs = Subscribers.load(tmp_path / "subscribers.json")
    bot.updates = [message(1, 1, "/start"), message(2, 2, "/start")]
    handle_updates(bot, subs)
    bot.sent.clear()
    return subs


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


# the details line names the offer's source, using the provider's label
def test_format_shows_source():
    offer = job(1)
    assert "ACME &lt;s.r.o.&gt; · Profesia" in format_verdict(offer)
    offer.source = "linkedin"
    assert "· LinkedIn" in format_verdict(offer)


# each offer is its own message per chat, and the returned ids say which message each chat got
def test_notify_sends_one_message_per_offer_and_returns_ids(tmp_path, monkeypatch):
    monkeypatch.setattr("offer_monitor.telegram.time.sleep", lambda s: None)
    bot = FakeBot()
    subs = two_subscribers(bot, tmp_path)

    changed, ids = notify(bot, subs, [job(1), job(2)])

    assert not changed
    assert len(bot.sent) == 4
    assert ids == {"u1": {1: 1, 2: 3}, "u2": {1: 2, 2: 4}}


# chats that blocked the bot are unsubscribed
def test_notify_drops_chats_that_blocked_the_bot(tmp_path):
    bot = FakeBot(blocked={2})
    subs = two_subscribers(bot, tmp_path)

    changed, ids = notify(bot, subs, [job(1)])

    assert changed
    assert ids == {"u1": {1: 1}}
    assert list(subs.chats) == [1]


# one network timeout is retried; a second one is raised
def test_call_retries_a_network_timeout_once():
    session = FlakySession(fail_times=1)
    assert Bot("token", session=session).get_updates(0) == []
    assert session.calls == 2

    session = FlakySession(fail_times=2)
    with pytest.raises(TelegramNetworkError):
        Bot("token", session=session).get_updates(0)
    assert session.calls == 2


# a network error's URL contains the token; it must not reach the error text, its cause, or the log
def test_network_error_hides_the_token(caplog):
    session = FlakySession(fail_times=2, message="Max retries exceeded with url: /botSECRET/getUpdates")
    with pytest.raises(TelegramNetworkError) as err:
        Bot("SECRET", session=session).get_updates(0)
    assert "SECRET" not in str(err.value)
    assert err.value.__cause__ is None
    assert "SECRET" not in caplog.text


# the error reply's code and text pick the typed error: token, blocked chat, rate limit, anything else
def test_error_reply_maps_to_its_class():
    assert isinstance(_telegram_error(401, "Unauthorized", None), TelegramAuthError)
    assert isinstance(_telegram_error(403, "Forbidden", None), TelegramBlockedError)
    assert isinstance(_telegram_error(400, "Bad Request: chat not found", None), TelegramBlockedError)
    rate = _telegram_error(429, "Too Many Requests", 5)
    assert isinstance(rate, TelegramRateLimitedError) and rate.retry_after == 5
    assert type(_telegram_error(500, "boom", None)) is TelegramError
