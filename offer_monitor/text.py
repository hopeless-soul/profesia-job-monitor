# Text helpers for log messages only; never use their output for stored or sent data


# Cut text to at most `limit` characters for a log line, adding "..." when it is cut
# shortcut: cuts mid-word and assumes limit >= 4, upgrade if logs ever need whole-word cuts
def shorten(text: str, limit: int = 120) -> str:
    # short enough already: return it unchanged
    if len(text) <= limit:
        return text
    # keep room for the "..." so the result never goes past limit
    return text[: limit - 3] + "..."
