# Telegram tables: subscribers, the getUpdates offset (telegram_state),
# and the message each chat got for each offer (offer_messages).

from __future__ import annotations

import sqlite3


### Remember the Offer Telegram `chat_id` & `msg_id` pairs;
def save_message_ids(conn: sqlite3.Connection, offer_id: str, message_ids: dict[int, int]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO offer_messages (offer_id, chat_id, message_id) VALUES (?, ?, ?)",
        [(offer_id, chat_id, message_id) for chat_id, message_id in message_ids.items()],
    )


### Telegram update offset and subscriber rows; the offset is None when nothing was ever saved
def load_subscribers(conn: sqlite3.Connection) -> tuple[int | None, list[dict]]:
    row = conn.execute("SELECT update_offset FROM telegram_state").fetchone()
    rows = conn.execute("SELECT chat_id, name, subscribed_at FROM subscribers ORDER BY chat_id")
    return (row[0] if row else None), [dict(r) for r in rows]


### Replace the stored update offset and subscribers with these (whole snapshot, like the old JSON file)
def save_subscribers(conn: sqlite3.Connection, offset: int, subscribers: list[dict]) -> None:
    conn.execute("INSERT OR REPLACE INTO telegram_state (id, update_offset) VALUES (1, ?)", (offset,))
    conn.execute("DELETE FROM subscribers")
    conn.executemany(
        "INSERT INTO subscribers (chat_id, name, subscribed_at) VALUES (:chat_id, :name, :subscribed_at)",
        subscribers,
    )
