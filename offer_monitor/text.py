# Text helpers for log messages only; never use their output for stored or sent data

def shorten(text: str, limit: int = 32) -> str:
    # short enough already: return it unchanged
    if len(text) <= limit:
        return text
    # keep room for the "..." so the result never goes past limit
    return text[: limit - 3] + "..."
