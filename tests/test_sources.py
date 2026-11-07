import pytest

from profesia_monitor.config import Config
from profesia_monitor.errors import ConfigError
from profesia_monitor.models import Job
from profesia_monitor.providers import SOURCE_LABELS, build_providers
from profesia_monitor.providers.linkedin import LinkedInConfig, LinkedInError, LinkedInProvider, LinkedInSearch
from profesia_monitor.providers.linkedin.config import parse_config as parse_linkedin
from profesia_monitor.providers.profesia_sk import ProfesiaConfig, ProfesiaError, ProfesiaProvider
from profesia_monitor.providers.profesia_sk.config import Query, parse_config as parse_profesia
from profesia_monitor.settings import RetryPolicy
from profesia_monitor.providers.base import SourceProviderError


# Profesia alone when only its config is loaded; both, in order, when LinkedIn is too
def test_build_providers_adds_each_source_when_its_config_is_loaded():
    profesia_only = build_providers(Config(profesia=ProfesiaConfig(searches=[Query(keyword="c#")])))
    both = build_providers(Config(
        profesia=ProfesiaConfig(searches=[Query(keyword="c#")]),
        linkedin=LinkedInConfig(searches=[LinkedInSearch(keywords="dev")]),
    ))

    assert [type(p) for p in profesia_only] == [ProfesiaProvider]
    assert [type(p) for p in both] == [ProfesiaProvider, LinkedInProvider]
    assert build_providers(Config()) == []


# each source's failure is a SourceProviderError, so run_once can catch them all in one place
def test_provider_errors_share_the_base_class():
    assert issubclass(ProfesiaError, SourceProviderError)
    assert issubclass(LinkedInError, SourceProviderError)


# Telegram labels come from the providers, one per source name
def test_source_labels_cover_every_provider():
    assert SOURCE_LABELS == {"profesia": "Profesia", "linkedin": "LinkedIn"}


# a job without a source is a bug, so building one fails
def test_job_requires_a_source():
    with pytest.raises(TypeError):
        Job(1, "Dev", "https://example.com/1")


# retry settings default to 3 attempts 30 s apart, are read per source file, and reject bad values
def test_retry_settings_are_read_and_validated():
    base = {"searches": [{"keyword": "c#"}]}

    assert parse_profesia(base).retry == RetryPolicy(attempts=3, delay=30.0)
    assert parse_profesia(base | {"retry": {"attempts": 5, "delay": 10}}).retry == RetryPolicy(attempts=5, delay=10.0)
    assert parse_linkedin({"searches": [{"keywords": "x"}], "retry": {"attempts": 2}}).retry == RetryPolicy(attempts=2, delay=30.0)
    with pytest.raises(ConfigError, match="retry.attempts"):
        parse_profesia(base | {"retry": {"attempts": 0}})
    with pytest.raises(ConfigError, match="retry.delay"):
        parse_profesia(base | {"retry": {"delay": -1}})
