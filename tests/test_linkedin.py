import asyncio
import json
from contextlib import asynccontextmanager, closing
from functools import partial
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from offer_monitor.models import Job
from offer_monitor.store import connect, insert_jobs
from offer_monitor.providers.linkedin import provider as linkedin
from offer_monitor.providers.linkedin.config import LinkedInConfig, LinkedInSearch
from offer_monitor.providers.linkedin.errors import LinkedInAuthError, LinkedInError, LinkedInResponseError, LinkedInUnavailableError
from offer_monitor.providers.linkedin.provider import LinkedInProvider, _call, _error_for, collect, parse_job_details

DETAILS = (Path(__file__).parent / "fixtures" / "linkedin_job_details.txt").read_text(encoding="utf-8")

CONFIG = LinkedInConfig(searches=[LinkedInSearch(keywords="python developer")], delay=0)
TWO_SEARCHES = LinkedInConfig(searches=[LinkedInSearch(keywords="a"), LinkedInSearch(keywords="b")], delay=0)


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


# Run collect against a session and return the jobs it gathered
def run_collect(session, config=CONFIG):
    jobs = []
    asyncio.run(collect(partial(_call, session), "linkedin", config, jobs))
    return jobs


# header, title, location and posted time, plus the work type lines before Apply
def test_parse_job_details_reads_header():
    job = parse_job_details(4484440903, DETAILS, "linkedin")

    assert (job.id, job.company, job.title, job.location, job.posted_text, job.source) == (
        4484440903, "Ciklum", "Back End JavaScript Developer", "Bratislava, Slovakia", "1 day ago", "linkedin",
    )
    assert job.labels == ["Hybrid", "Full-time"]


# text without a header line is a bad job, not a crash
def test_parse_job_details_rejects_unexpected_text():
    with pytest.raises(ValueError):
        parse_job_details(1, "Sign in\n\nJoin now", "linkedin")


# details are read for every found ID
def test_collect_reads_every_job():
    jobs = run_collect(FakeSession(replies([1, 2, 3])))

    assert [j.id for j in jobs] == [1, 2, 3]


# one failing search is skipped; the IDs of the other searches are still read
def test_collect_keeps_other_searches_when_one_fails():
    search_replies = iter([reply(text="Search timed out", error=True), reply({"job_ids": [5]})])
    session = FakeSession(replies([]) | {"search_jobs": lambda args: next(search_replies)})

    assert [j.id for j in run_collect(session, TWO_SEARCHES)] == [5]


# details calls are spaced by the config's delay, with no pause before the first one
def test_collect_pauses_between_detail_requests(monkeypatch):
    pauses = []

    async def record(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(linkedin.asyncio, "sleep", record)

    run_collect(FakeSession(replies([1, 2, 3])), CONFIG.model_copy(update={"delay": 0.5}))

    assert pauses == [0.5, 0.5]


# a login prompt stops the whole LinkedIn step at once, instead of failing each search and job in turn
def test_login_needed_stops_the_run():
    session = FakeSession({"search_jobs": lambda args: reply(text="Please log in", error=True)})

    with pytest.raises(LinkedInAuthError):
        run_collect(session, TWO_SEARCHES)
    assert len(session.calls) == 1  # the second search was never tried


# a reply that is not JSON is a response error
def test_invalid_json_is_a_response_error():
    session = FakeSession({"search_jobs": lambda args: reply(text="<html>not json</html>")})

    with pytest.raises(LinkedInResponseError):
        asyncio.run(_call(session, "search_jobs", {}))


# login text is an auth error; setup text is an unavailable server; other text is a generic LinkedIn error
def test_server_error_text_is_classified():
    assert isinstance(_error_for("Please log in to LinkedIn"), LinkedInAuthError)
    assert isinstance(_error_for("Captcha required"), LinkedInAuthError)
    assert isinstance(_error_for("Downloading the browser, please wait"), LinkedInUnavailableError)
    assert type(_error_for("Search timed out")) is LinkedInError


# the mcp floor: the code reads CallToolResult.is_error, which mcp 1.x does not have (the fakes would hide that)
def test_mcp_result_has_is_error_field():
    assert "is_error" in CallToolResult.model_fields


# the provider reads jobs through the session it opens, tagged with its name
def test_provider_fetches_jobs_through_its_session():
    provider = LinkedInProvider(CONFIG, open_session=opener(FakeSession(replies([1, 2]))))

    jobs = provider.fetch()

    assert [(j.source, j.id) for j in jobs] == [("linkedin", 1), ("linkedin", 2)]


# a server that cannot start is an unavailable source, which fetch_source retries
def test_provider_turns_start_failures_into_unavailable():
    def missing_uvx():
        raise FileNotFoundError("uvx")

    with pytest.raises(LinkedInUnavailableError):
        LinkedInProvider(CONFIG, open_session=missing_uvx).fetch()


# get_known returns only this source's stored IDs, and nothing without a database
def test_get_known_reads_stored_ids(tmp_path):
    db = tmp_path / "offers.db"
    with closing(connect(db)) as conn, conn:
        insert_jobs(conn, [
            Job(id=2, title="Dev", url="https://example.com/2", source="linkedin"),
            Job(id=3, title="Dev", url="https://example.com/3", source="profesia"),
        ])

    assert LinkedInProvider(CONFIG, db=db).get_known([1, 2, 3]) == {2}
    assert LinkedInProvider(CONFIG).get_known([1]) == set()


# on timeout the jobs read so far are returned, not lost
def test_provider_keeps_jobs_read_before_the_time_limit():
    provider = LinkedInProvider(CONFIG, open_session=opener(SlowSession(replies([1, 2]))))
    provider.time_limit = 0.2

    assert [j.id for j in provider.fetch()] == [1]
