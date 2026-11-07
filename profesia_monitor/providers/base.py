# Base class for job sources (Profesia.sk, LinkedIn, ...) and the errors they raise. Imports no provider, so providers can import it.

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

from ..models import Job
from ..settings import RetryPolicy


# Base error for a source that failed this run; fetch_source retries it when retryable, then logs it and moves on
class SourceProviderError(RuntimeError):
    # whether a retry can help; causes that a retry cannot fix set this to False
    retryable: ClassVar[bool] = True

    def __init__(self, message: str = "", *, retry_after: float | None = None):
        super().__init__(message)
        # seconds the source asked us to wait (Retry-After), when it said so
        self.retry_after = retry_after


# The source did not answer: connection failed, timed out, returned 5xx, or the server did not start
class SourceUnavailableError(SourceProviderError):
    pass


# HTTP 429: the source asks us to slow down; retry_after carries the wait it asked for
class SourceRateLimitedError(SourceProviderError):
    pass


# The source replied, but not in a usable shape (invalid JSON, missing fields)
class SourceResponseError(SourceProviderError):
    pass


# HTTP 401/403 or an anti-bot page: a retry gets the same answer
class SourceBlockedError(SourceProviderError):
    retryable = False


# Login or verification is required; only a person can fix that
class SourceAuthError(SourceProviderError):
    retryable = False


# The page structure changed and nothing parses; a retry sees the same page
class SourceLayoutError(SourceProviderError):
    retryable = False


# Base class for job sources
class SourceProvider(ABC):
    # Written to Job.source and the offers table; set once per provider
    name: ClassVar[str]
    # Shown in Telegram messages
    label: ClassVar[str]
    # Retry settings used by fetch_source (called from run_once) when fetch() raises SourceProviderError
    retry: RetryPolicy = RetryPolicy()

    # New offers only, each with source=self.name. `known(ids)` returns the IDs already stored.
    # Raises SourceProviderError when the source failed for this run.
    @abstractmethod
    def fetch(self, known: Callable[[list[int]], set[int]]) -> list[Job]: ...
