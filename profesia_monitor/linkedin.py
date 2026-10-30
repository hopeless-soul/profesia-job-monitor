# LinkedIn job source: searches through the LinkedIn MCP server (mcp-server-linkedin), which drives
# a browser logged in as the user. Needs `uv` installed and one manual login in a console run.

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from functools import partial

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import LinkedInConfig
from .models import Job

log = logging.getLogger(__name__)

# Same launch command as the Claude Code plugin; first start downloads the server and Chromium
SERVER = StdioServerParameters(
    command="uvx",
    args=["mcp-server-linkedin@latest", "--transport", "stdio"],
    env={"UV_HTTP_TIMEOUT": "300"},
)
TIME_LIMIT = 300  # seconds for the whole LinkedIn step, so a stuck browser can't block the loop
JOB_URL = "https://www.linkedin.com/jobs/view/{id}/"

# Work type lines shown under a job's header
WORK_LABELS = {"Remote", "Hybrid", "On-site", "Full-time", "Part-time", "Contract", "Temporary", "Internship"}

# MCP tool call: (tool name, arguments) -> decoded JSON result; raises LinkedInError
Call = Callable[[str, dict], Awaitable[dict]]


class LinkedInError(RuntimeError):
    pass


# Job from get_job_details text: company, title, "location · posted · ...", then work type lines
def parse_job_details(job_id: int, text: str) -> Job:
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
        source="linkedin",
    )


# Search every query, then read details of the IDs not stored yet; jobs are appended as they parse,
# so the caller keeps them when the time limit cuts the run short
async def collect(call: Call, config: LinkedInConfig, known: Callable[[list[int]], set[int]], jobs: list[Job]) -> None:
    # job IDs from every search, deduplicated, in result order
    ids: dict[int, None] = {}
    for search in config.searches:
        filters = {"work_type": search.work_type, "experience_level": search.experience_level, "job_type": search.job_type}
        args = {"keywords": search.keywords, "date_posted": config.date_posted, "max_pages": config.max_pages}
        result = await call("search_jobs", args | {k: v for k, v in filters.items() if v})
        found = [int(i) for i in result.get("job_ids", [])]
        log.info("linkedin search %r: %d job ids", search.keywords, len(found))
        ids.update(dict.fromkeys(found))

    # details only for new jobs; a failed one is skipped and retried next run (it isn't stored)
    skip = known(list(ids))
    log.info("linkedin: %d unique job ids, %d already stored, %d to read", len(ids), len(skip), len(ids) - len(skip))
    for job_id in ids:
        if job_id in skip:
            continue
        try:
            result = await call("get_job_details", {"job_id": str(job_id)})
            job = parse_job_details(job_id, result["sections"]["job_posting"])
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
    return json.loads(text)


# Start the server, collect, stop the server
async def _fetch(config: LinkedInConfig, known: Callable[[list[int]], set[int]], jobs: list[Job]) -> None:
    async with stdio_client(SERVER) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        log.info("linkedin MCP server started")
        await collect(partial(_call, session), config, known, jobs)


# New LinkedIn jobs for the configured searches. `known(ids)` returns the IDs already stored.
# Never raises: a login prompt, timeout or server failure is logged and the jobs parsed so far returned.
def fetch_jobs(config: LinkedInConfig, known: Callable[[list[int]], set[int]]) -> list[Job]:
    jobs: list[Job] = []
    try:
        asyncio.run(asyncio.wait_for(_fetch(config, known, jobs), TIME_LIMIT))
    except TimeoutError:
        log.warning("linkedin stopped after %ss, keeping %d jobs", TIME_LIMIT, len(jobs))
    except Exception as e:
        log.warning("linkedin skipped: %s", e)
    return jobs
