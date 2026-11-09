# Lesson 2: tests for _command, which pulls the bot command out of a Telegram message text

import pytest

from offer_monitor.telegram import _command


# the command word is lowercased; the @BotName suffix and any arguments are dropped; plain text is no command
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start", "/start"),
        ("/STOP", "/stop"),
        ("/start@OfferMonitorBot", "/start"),
        ("/start@OfferMonitorBot payload", "/start"),
        ("hello /start", ""),
        ("", ""),
    ],
    ids=["plain", "uppercase", "bot-suffix", "suffix-and-args", "not-first", "empty"],
)
def test_command(text, expected):
    assert _command(text) == expected
