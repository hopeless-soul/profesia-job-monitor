# Shared interface for job sources (Profesia.sk, LinkedIn, ...). Imports no provider, so providers can import it.

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

from .models import Job
from .settings import RetryPolicy


# Base error for a source that failed this run; run_once retries it, then logs it and moves on
class SourceProviderError(RuntimeError):
    pass


# Base class for job sources
class SourceProvider(ABC):
    # Written to Job.source and the offers table; set once per provider
    name: ClassVar[str]
    # Shown in Telegram messages
    label: ClassVar[str]
    # Retry settings used by run_once when fetch() raises SourceProviderError
    retry: RetryPolicy = RetryPolicy()

    # New offers only, each with source=self.name. `known(ids)` returns the IDs already stored.
    # Raises SourceProviderError when the source failed for this run.
    @abstractmethod
    def fetch(self, known: Callable[[list[int]], set[int]]) -> list[Job]: ...
