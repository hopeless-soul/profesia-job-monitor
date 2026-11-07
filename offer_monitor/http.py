# Shared HTTP helpers, used by the job sources and the offer fetcher
import requests

# Browser User-Agent for HTTP requests (Profesia scraper and the agents' page fetch tool)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# Seconds from a Retry-After header in its seconds form; None when missing or an HTTP date
def retry_after_seconds(resp: requests.Response) -> float | None:
    try:
        return max(0.0, float(resp.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None
