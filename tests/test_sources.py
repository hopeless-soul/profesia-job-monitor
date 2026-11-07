import pytest

from offer_monitor.errors import ConfigError
from offer_monitor.models import Job
from offer_monitor.providers import build_providers
from offer_monitor.providers.linkedin import LinkedInProvider
from offer_monitor.providers.linkedin.config import parse_config as parse_linkedin
from offer_monitor.providers.linkedin.errors import LinkedInAuthError
from offer_monitor.providers.profesia_sk import ProfesiaConfig, ProfesiaProvider
from offer_monitor.providers.profesia_sk.config import Query, parse_config as parse_profesia
from offer_monitor.providers.profesia_sk.errors import ProfesiaBlockedError, ProfesiaRateLimitedError, ProfesiaUnavailableError
from offer_monitor.settings import RetryPolicy

PROFESIA = ProfesiaConfig(searches=[Query(keyword="c#")])


# only the listed providers are built, in list order, each from its own file; an unlisted file is ignored
def test_build_providers_builds_listed_sources_in_order(tmp_path):
    (tmp_path / "profesia_sk.yaml").write_text("searches:\n  - keyword: c#\n", encoding="utf-8")
    (tmp_path / "linkedin.yaml").write_text("searches:\n  - keywords: dev\n", encoding="utf-8")

    assert build_providers([], tmp_path) == []
    [profesia] = build_providers(["profesia"], tmp_path)
    assert type(profesia) is ProfesiaProvider and [q.keyword for q in profesia.config.searches] == ["c#"]
    assert [type(p) for p in build_providers(["linkedin", "profesia"], tmp_path)] == [LinkedInProvider, ProfesiaProvider]


# a listed provider without its file is an error that names the file
def test_build_providers_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="providers lists 'linkedin', but .*linkedin.yaml is missing"):
        build_providers(["linkedin"], tmp_path)


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
