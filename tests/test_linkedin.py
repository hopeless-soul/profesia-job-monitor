import asyncio
import json
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from profesia_monitor.errors import ConfigError
from profesia_monitor.providers.linkedin import provider as linkedin
from profesia_monitor.providers.linkedin.config import LinkedInConfig, LinkedInSearch, parse_config
from profesia_monitor.providers.linkedin.provider import LinkedInError, LinkedInProvider, _call, collect, parse_job_details

DETAILS = (Path(__file__).parent / "fixtures" / "linkedin_job_details.txt").read_text(encoding="utf-8")


# Stands in for mcp ClientSession: `replies` maps a tool name to a function(args) -> CallToolResult
class FakeSession:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return self.replies[name](args)


# A details call that takes too long for the time limit
class SlowSession(FakeSession):
    async def call_tool(self, name, args):
        if args.get("job_id") == "2":
            await asyncio.sleep(5)
        return await super().call_tool(name, args)


# A tool reply shaped like the MCP server's: JSON text, or an error text
def reply(payload=None, *, text=None, error=False):
    body = text if text is not None else json.dumps(payload)
    return CallToolResult(content=[TextContent(type="text", text=body)], is_error=error)


# Standard replies: search returns the given IDs, every details call returns the fixture text
def replies(search_ids):
    return {
        "search_jobs": lambda args: reply({"job_ids": search_ids}),
        "get_job_details": lambda args: reply({"sections": {"job_posting": DETAILS}}),
    }


# An open_session stand-in that yields the given session
def opener(session):
    @asynccontextmanager
    async def open_session():
        yield session

    return open_session


CONFIG = LinkedInConfig(searches=[LinkedInSearch(keywords="python developer")], delay=0)


# header, title, location and posted time, plus the work type lines before Apply
def test_parse_job_details_reads_header():
    job = parse_job_details(4484440903, DETAILS, "linkedin")

    assert (job.id, job.company, job.title, job.location, job.posted_text) == (
        4484440903, "Ciklum", "Back End JavaScript Developer", "Bratislava, Slovakia", "1 day ago",
    )
    assert job.labels == ["Hybrid", "Full-time"]
    assert job.source == "linkedin"


# text without a header line is a bad job, not a crash
def test_parse_job_details_rejects_unexpected_text():
    with pytest.raises(ValueError):
        parse_job_details(1, "Sign in\n\nJoin now", "linkedin")


# details are read only for IDs not stored yet, and each job carries the provider's source
def test_collect_reads_only_new_jobs():
    session = FakeSession(replies([1, 2, 3]))
    jobs = []

    asyncio.run(collect(partial(_call, session), "linkedin", CONFIG, 0, lambda ids: {2}, jobs))

    assert [j.id for j in jobs] == [1, 3]
    assert {j.source for j in jobs} == {"linkedin"}


# one failing search is skipped; the IDs of the other searches are still read
def test_collect_keeps_other_searches_when_one_fails():
    search_replies = iter([reply(text="Please log in", error=True), reply({"job_ids": [5]})])
    session = FakeSession({
        "search_jobs": lambda args: next(search_replies),
        "get_job_details": lambda args: reply({"sections": {"job_posting": DETAILS}}),
    })
    config = LinkedInConfig(searches=[LinkedInSearch(keywords="a"), LinkedInSearch(keywords="b")], delay=0)
    jobs = []

    asyncio.run(collect(partial(_call, session), "linkedin", config, 0, lambda ids: set(), jobs))

    assert [j.id for j in jobs] == [5]


# details calls are spaced by `delay`, with no pause before the first one
def test_collect_pauses_between_detail_requests(monkeypatch):
    pauses = []

    async def record(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(linkedin.asyncio, "sleep", record)
    config = LinkedInConfig(searches=[LinkedInSearch(keywords="dev")], delay=0.5)

    asyncio.run(collect(partial(_call, FakeSession(replies([1, 2, 3]))), "linkedin", config, 0.5, lambda ids: set(), []))

    assert pauses == [0.5, 0.5]


# a server error result becomes LinkedInError with the server's text
def test_call_turns_error_results_into_linkedin_error():
    session = FakeSession({"search_jobs": lambda args: reply(text="Please log in", error=True)})

    with pytest.raises(LinkedInError, match="Please log in"):
        asyncio.run(_call(session, "search_jobs", {}))


# a reply that is not JSON is a LinkedIn failure, not a JSON error
def test_call_turns_invalid_json_into_linkedin_error():
    session = FakeSession({"search_jobs": lambda args: reply(text="<html>not json</html>")})

    with pytest.raises(LinkedInError):
        asyncio.run(_call(session, "search_jobs", {}))


# the provider reads jobs through the session it opens
def test_provider_fetches_jobs_through_its_session():
    provider = LinkedInProvider(CONFIG, open_session=opener(FakeSession(replies([1, 2]))))

    jobs = provider.fetch(lambda ids: set())

    assert [j.id for j in jobs] == [1, 2]
    assert {j.source for j in jobs} == {"linkedin"}


# a server that cannot start is a LinkedInError, which run_once logs
def test_provider_turns_start_failures_into_linkedin_error():
    def missing_uvx():
        raise FileNotFoundError("uvx")

    provider = LinkedInProvider(CONFIG, open_session=missing_uvx)

    with pytest.raises(LinkedInError):
        provider.fetch(lambda ids: set())


# on timeout the jobs read so far are returned, not lost
def test_provider_keeps_jobs_read_before_the_time_limit():
    provider = LinkedInProvider(CONFIG, open_session=opener(SlowSession(replies([1, 2]))))
    provider.time_limit = 0.2

    jobs = provider.fetch(lambda ids: set())

    assert [j.id for j in jobs] == [1]


# the LinkedIn file takes an optional delay, default 1.5 s, and rejects negatives
def test_config_reads_linkedin_delay():
    base = {"searches": [{"keywords": "x"}]}

    assert parse_config(base).delay == 1.5
    assert parse_config(base | {"delay": 2}).delay == 2.0
    with pytest.raises(ConfigError):
        parse_config(base | {"delay": -1})


# the mcp floor: the code reads CallToolResult.is_error, which mcp 1.x does not have
def test_mcp_result_has_is_error_field():
    assert "is_error" in CallToolResult.model_fields
