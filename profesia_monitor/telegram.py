"""Telegram notifications. Users subscribe with /start and unsubscribe with /stop.

Uses long polling (getUpdates), so no webhook or public server is needed.
subscribers.json keeps the chat list plus the update offset, so each command
is handled once.
"""

from __future__ import annotations

import html
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from .models import Job
from .store import now_iso

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE_LEN = 4096


class TelegramError(RuntimeError):
    def __init__(self, code: int, description: str, retry_after: int | None = None):
        super().__init__(f"Telegram API error {code}: {description}")
        self.code = code
        self.retry_after = retry_after


class Bot:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.token = token
        self.session = session or requests.Session()

    def call(self, method: str, http_timeout: float = 15, **params) -> object:
        for attempt in range(2):
            try:
                resp = self.session.post(API_URL.format(token=self.token, method=method), json=params, timeout=http_timeout)
            except requests.exceptions.RequestException as e:
                if attempt == 0:
                    log.warning("%s request failed (%s), retrying", method, e)
                    continue
                raise
            data = resp.json()
            if data.get("ok"):
                return data["result"]
            retry_after = (data.get("parameters") or {}).get("retry_after")
            error = TelegramError(data.get("error_code", resp.status_code), data.get("description", ""), retry_after)
            if error.code == 429 and retry_after and attempt == 0:
                log.warning("rate limited, retrying in %ss", retry_after)
                time.sleep(retry_after)
                continue
            raise error
        raise AssertionError("unreachable")

    def get_updates(self, offset: int, timeout: int = 0) -> list[dict]:
        """Long-polls for up to `timeout` seconds (0 = return immediately)."""
        return self.call("getUpdates", http_timeout=timeout + 10, offset=offset, timeout=timeout,
                         allowed_updates=["message"])

    def send(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                  link_preview_options={"is_disabled": True})


@dataclass
class Subscriber:
    chat_id: int
    name: str | None = None
    subscribed_at: str | None = None


@dataclass
class Subscribers:
    path: Path
    offset: int = 0
    chats: dict[int, Subscriber] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Subscribers:
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        subs = [Subscriber(**s) for s in data.get("subscribers", [])]
        return cls(path, data.get("offset", 0), {s.chat_id: s for s in subs})

    def save(self) -> None:
        data = {"offset": self.offset, "subscribers": [asdict(s) for s in self.chats.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise


def _command(text: str) -> str:
    """'/start@MyBot payload' -> '/start'."""
    return text.split()[0].split("@")[0].lower() if text.startswith("/") else ""


def handle_updates(bot: Bot, subs: Subscribers, timeout: int = 0) -> bool:
    """Fetch pending messages and apply /start and /stop. Returns True if subs changed."""
    updates = bot.get_updates(subs.offset, timeout)
    changed = False
    for update in updates:
        subs.offset = max(subs.offset, update["update_id"] + 1)
        changed = True
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        cmd = _command(message.get("text") or "")
        if chat_id is None or not cmd:
            continue
        if cmd == "/start":
            if chat_id not in subs.chats:
                name = chat.get("username") or chat.get("title") or chat.get("first_name")
                subs.chats[chat_id] = Subscriber(chat_id, name, now_iso())
                log.info("subscribed %s (%s)", chat_id, name)
            reply = "Subscribed. You will get new Profesia.sk offers here. Send /stop to unsubscribe."
        elif cmd == "/stop":
            if subs.chats.pop(chat_id, None):
                log.info("unsubscribed %s", chat_id)
            reply = "Unsubscribed. Send /start to subscribe again."
        else:
            reply = "Commands: /start to subscribe, /stop to unsubscribe."
        try:
            bot.send(chat_id, reply)
        except TelegramError as e:
            log.warning("reply to %s failed: %s", chat_id, e)
    return changed


def format_job(job: Job) -> str:
    lines = [f'<b><a href="{html.escape(job.url)}">{html.escape(job.title)}</a></b>']
    details = " · ".join(html.escape(x) for x in (job.company, job.location) if x)
    if details:
        lines.append(details)
    if job.salary:
        lines.append(f"💶 {html.escape(job.salary.raw)}")
    return "\n".join(lines)


def build_messages(jobs: list[Job]) -> list[str]:
    """Pack jobs into as few messages as fit Telegram's length limit."""
    messages: list[str] = []
    current = f"🆕 <b>{len(jobs)} new offer{'s' if len(jobs) != 1 else ''} on Profesia.sk</b>"
    for block in map(format_job, jobs):
        if len(current) + 2 + len(block) > MAX_MESSAGE_LEN:
            messages.append(current)
            current = block
        else:
            current += "\n\n" + block
    messages.append(current)
    return messages


def notify(bot: Bot, subs: Subscribers, jobs: list[Job]) -> bool:
    """Send jobs to every subscriber. Drops chats that blocked the bot. Returns True if subs changed."""
    if not jobs or not subs.chats:
        return False
    messages = build_messages(jobs)
    changed = False
    for chat_id in list(subs.chats):
        for text in messages:
            try:
                bot.send(chat_id, text)
            except TelegramError as e:
                if e.code == 403 or "chat not found" in str(e):  # blocked, kicked or chat gone
                    log.warning("removing subscriber %s: %s", chat_id, e)
                    del subs.chats[chat_id]
                    changed = True
                else:
                    log.warning("send to %s failed: %s", chat_id, e)
                break
            if len(messages) > 1:
                time.sleep(1)  # Telegram allows ~1 message/second per chat
    return changed
