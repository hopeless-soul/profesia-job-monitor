import pytest

from offer_monitor.config import Config
from offer_monitor.errors import ConfigError
from offer_monitor.models import Job
from offer_monitor.providers import build_providers
from offer_monitor.providers.linkedin import LinkedInConfig, LinkedInProvider, LinkedInSearch
from offer_monitor.providers.linkedin.config import parse_config as parse_linkedin
from offer_monitor.providers.linkedin.errors import LinkedInAuthError
from offer_monitor.providers.profesia_sk import ProfesiaConfig, ProfesiaProvider
from offer_monitor.providers.profesia_sk.config import Query, parse_config as parse_profesia
from offer_monitor.providers.profesia_sk.errors import ProfesiaBlockedError, ProfesiaRateLimitedError, ProfesiaUnavailableError
from offer_monitor.settings import RetryPolicy

PROFESIA = ProfesiaConfig(searches=[Query(keyword="c#")])


# no source without its config; Profesia alone, or both in order when LinkedIn is loaded too
def test_build_providers_adds_each_source_when_its_config_is_loaded():
    linkedin = LinkedInConfig(searches=[LinkedInSearch(keywords="dev")])

    assert build_providers(Config()) == []
    assert [type(p) for p in build_providers(Config(profesia=PROFESIA))] == [ProfesiaProvider]
    assert [type(p) for p in build_providers(Config(profesia=PROFESIA, linkedin=linkedin))] == [ProfesiaProvider, LinkedInProvider]


# dedup_fetched keeps the first copy of a repeated (source, id), in order; the same id on another source stays
def test_dedup_fetched_keeps_first_copy_in_order():
    jobs = [
        Job(1, "A", "https://x/1", source="profesia"),
        Job(2, "B", "https://x/2", source="profesia"),
        Job(1, "A again", "https://x/1", source="profesia"),
        Job(1, "L", "https://y/1", source="linkedin"),
    ]

    assert [(j.source, j.id, j.title) for j in ProfesiaProvider(PROFESIA).dedup_fetched(jobs)] == [
        ("profesia", 1, "A"), ("profesia", 2, "B"), ("linkedin", 1, "L"),
    ]


# db_dedup drops the ids `known` reports as stored
def test_db_dedup_drops_stored_ids():
    jobs = [Job(i, f"Dev {i}", f"https://x/{i}", source="profesia") for i in (1, 2, 3)]

    assert [j.id for j in ProfesiaProvider(PROFESIA).db_dedup(jobs, lambda ids: {2})] == [1, 3]


# retry settings default to 3 attempts 30 s apart, are read per source file, and reject bad values
def test_retry_settings_are_read_and_validated():
    base = {"searches": [{"keyword": "c#"}]}

    assert parse_profesia(base).retry == RetryPolicy(attempts=3, delay=30)
    assert parse_profesia(base | {"retry": {"attempts": 5, "delay": 10}}).retry == RetryPolicy(attempts=5, delay=10)
    assert parse_linkedin({"searches": [{"keywords": "x"}], "retry": {"attempts": 2}}).retry == RetryPolicy(attempts=2, delay=30)
    with pytest.raises(ConfigError, match="retry.attempts"):
        parse_profesia(base | {"retry": {"attempts": 0}})
    with pytest.raises(ConfigError, match="retry.delay"):
        parse_profesia(base | {"retry": {"delay": -1}})


# a retry helps an outage or a rate limit, not a block or a login prompt
def test_only_fixable_errors_are_retryable():
    assert ProfesiaUnavailableError.retryable and ProfesiaRateLimitedError.retryable
    assert not ProfesiaBlockedError.retryable and not LinkedInAuthError.retryable
