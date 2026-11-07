from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar
from ..models import Job
from ..settings import RetryPolicy

"""
Section: Base errors
"""


class SourceProviderError(RuntimeError):
    """
    Base error for a source that failed this run;
    fetch_source retries it when retryable;
    """

    retryable: ClassVar[bool] = True

    def __init__(self, message: str = "", *, retry_after: float | None = None):
        super().__init__(message)
        # seconds the source asked us to wait (Retry-After), when it said so
        self.retry_after = retry_after


# The source did not answer:
# connection_failed, timed_out, returned_5xx, or the server did not start;
class SourceUnavailableError(SourceProviderError):
    pass


# HTTP 429: rate limit;
class SourceRateLimitedError(SourceProviderError):
    pass


# The source replied, but not in a usable shape (invalid JSON, missing fields);
class SourceResponseError(SourceProviderError):
    pass


# HTTP 401/403 or an anti-bot page;
class SourceBlockedError(SourceProviderError):
    retryable = False


# Login or verification is required;
class SourceAuthError(SourceProviderError):
    retryable = False


# The page structure changed;
class SourceLayoutError(SourceProviderError):
    retryable = False

"""
Section: Base Class
"""

class SourceProvider(ABC):
    """
    Abstract Base Class
    """

    name: ClassVar[str]
    label: ClassVar[str]
    retry: RetryPolicy = RetryPolicy()

    @abstractmethod
    def fetch(self) -> list[Job]:
        """
        Return the offers found this run, stored or not; `db_dedup` drops the stored ones later;

        Each offer has `source=self.name`;

        Raise `SourceProviderError` if the source failed this run;
        """
        ...

    def dedup_fetched(self, jobs: list[Job]) -> list[Job]:
        """
        Dedup offers among the run; keeps the first copy, in order
        """
        first: dict[tuple[str, int], Job] = {}
        for job in jobs:
            # NOTE: `first[key] = job` Saves the last copy; `.setdefault` will save the first copy;
            first.setdefault((job.source, job.id), job)
        return list(first.values())

    def db_dedup(
        self, jobs: list[Job], known: Callable[[list[int]], set[int]]
    ) -> list[Job]:
        """
        Dedup offers against the database; keeps the first copy, in order. Offers not stored yet;
         `known(ids)` returns the IDs already stored. Runs after every source has fetched.
        """
        stored = known([job.id for job in jobs])
        return [job for job in jobs if job.id not in stored]
