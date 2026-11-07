# LinkedIn job source: searches through the LinkedIn MCP server (mcp-server-linkedin), which drives
# a browser logged in as the user. Needs `uv` installed and one manual login in a console run.

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import partial
from typing import ClassVar

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ...models import Job
from ..base import SourceProvider, SourceProviderError
from .config import LinkedInConfig

log = logging.getLogger(__name__)

# Same launch command as the Claude Code plugin; first start downloads the server and Chromium
SERVER = StdioServerParameters(
    command="uvx",
    args=["mcp-server-linkedin@latest", "--transport", "stdio"],
    env={"UV_HTTP_TIMEOUT": "300"},
)
JOB_URL = "https://www.linkedin.com/jobs/view/{id}/"

# Work type lines shown under a job's header
WORK_LABELS = {"Remote", "Hybrid", "On-site", "Full-time", "Part-time", "Contract", "Temporary", "Internship"}

# MCP tool call: (tool name, arguments) -> decoded JSON result; raises LinkedInError
Call = Callable[[str, dict], Awaitable[dict]]

# Opens an MCP session as an async context manager; tests pass a fake one
SessionOpener = Callable[[], AbstractAsyncContextManager[ClientSession]]


# LinkedIn failure for this run (login needed, bad reply, server did not start)
class LinkedInError(SourceProviderError):
    pass


# Start the MCP server over stdio and initialise the session
@asynccontextmanager
async def stdio_session() -> AsyncIterator[ClientSession]:
    async with stdio_client(SERVER) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        log.info("linkedin MCP server started")
        yield session


# Job from get_job_details text: company, title, "location · posted · ...", then work type lines
def parse_job_details(job_id: int, text: str, source: str) -> Job:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3 or " · " not in lines[2]:
        raise ValueError(f"unexpected job header: {lines[:3]}")
    location, posted = lines[2].split(" · ")[:2]

    # work type lines sit between the header and the Apply button
    labels = []
    for line in lines[3:]:
        if line == "Apply":
            break
        if line in WORK_LABELS:
            labels.append(line)

    return Job(
        id=job_id,
        title=lines[1],
        url=JOB_URL.format(id=job_id),
        company=lines[0],
        location=location,
        labels=labels,
        posted_text=posted,
        source=source,
    )


# Search every query, then read details of the IDs not stored yet; jobs are appended as they parse,
# so the caller keeps them when the time limit cuts the run short
async def collect(
    call: Call,
    source: str,
    config: LinkedInConfig,
    delay: float,
    known: Callable[[list[int]], set[int]],
    jobs: list[Job],
) -> None:
    # job IDs from every search, deduplicated, in result order; a failed search is skipped, the others still count
    ids: dict[int, None] = {}
    for search in config.searches:
        filters = {"work_type": search.work_type, "experience_level": search.experience_level, "job_type": search.job_type}
        args = {"keywords": search.keywords, "date_posted": config.date_posted, "max_pages": config.max_pages}
        try:
            result = await call("search_jobs", args | {k: v for k, v in filters.items() if v})
        except LinkedInError as e:
            log.warning("linkedin search %r failed: %s", search.keywords, e)
            continue
        found = [int(i) for i in result.get("job_ids", [])]
        log.info("linkedin search %r: %d job ids", search.keywords, len(found))
        ids.update(dict.fromkeys(found))

    # details only for new jobs; a failed one is skipped and retried next run (it isn't stored)
    skip = known(list(ids))
    todo = [job_id for job_id in ids if job_id not in skip]
    log.info("linkedin: %d unique job ids, %d already stored, %d to read", len(ids), len(skip), len(todo))
    for n, job_id in enumerate(todo):
        # pause between detail requests so the logged-in browser isn't hit in a burst
        if n:
            await asyncio.sleep(delay)
        try:
            result = await call("get_job_details", {"job_id": str(job_id)})
            job = parse_job_details(job_id, result["sections"]["job_posting"], source)
            log.info(
                "linkedin job %s parsed: %s | %s | %s | %s | %s",
                job_id, job.title, job.company, job.location, job.posted_text, ", ".join(job.labels) or "-",
            )
            jobs.append(job)
        except (LinkedInError, KeyError, ValueError) as e:
            log.warning("linkedin job %s skipped: %s", job_id, e)


# Call one MCP tool; the server reports problems (login needed, setup running) as error results
async def _call(session: ClientSession, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in result.content)
    if result.is_error:
        raise LinkedInError(text)
    # a reply that is not JSON is a server problem, so it counts as a LinkedIn failure
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LinkedInError(f"invalid reply from {name}: {e}") from e


class LinkedInProvider(SourceProvider):
    name = "linkedin"
    label = "LinkedIn"
    time_limit: ClassVar[float] = 300  # seconds for the whole LinkedIn step, so a stuck browser can't block the loop

    def __init__(self, config: LinkedInConfig, open_session: SessionOpener = stdio_session):
        self.config, self.open_session = config, open_session
        self.retry = config.retry

    # Blocking wrapper around one asyncio run. A timeout returns the jobs parsed so far.
    # Any other failure to talk to the server becomes LinkedInError.
    def fetch(self, known: Callable[[list[int]], set[int]]) -> list[Job]:
        jobs: list[Job] = []
        try:
            asyncio.run(asyncio.wait_for(self._run(known, jobs), self.time_limit))
        except TimeoutError:
            log.warning("linkedin stopped after %ss, keeping %d jobs", self.time_limit, len(jobs))
        except SourceProviderError:
            raise
        except Exception as e:
            raise LinkedInError(f"server failed: {e}") from e
        return jobs

    # Open the session, then collect
    async def _run(self, known: Callable[[list[int]], set[int]], jobs: list[Job]) -> None:
        async with self.open_session() as session:
            await collect(partial(_call, session), self.name, self.config, self.config.delay, known, jobs)
