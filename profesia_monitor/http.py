# Shared HTTP helpers, used by the job sources and the offer fetcher
import requests


# Seconds from a Retry-After header in its seconds form; None when missing or an HTTP date
def retry_after_seconds(resp: requests.Response) -> float | None:
    try:
        return max(0.0, float(resp.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None
