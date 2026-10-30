import json
from pathlib import Path

import jsonschema
import pytest

from profesia_monitor.__main__ import build_parser, resolve_config
from profesia_monitor.config import ConfigError, LinkedInConfig, LinkedInSearch, load_config, parse_config
from profesia_monitor.scraper import Query

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "data" / "config.example.json"
SCHEMA = json.loads((ROOT / "schemas" / "config.schema.json").read_text(encoding="utf-8"))


# the example config matches the schema; db and backstory resolve next to it
def test_example_config_is_valid():
    jsonschema.validate(json.loads(EXAMPLE.read_text(encoding="utf-8")), SCHEMA)
    config = load_config(EXAMPLE)
    assert config.db == ROOT / "data" / "jobs.db"
    assert config.backstory == ROOT / "data" / "backstory.md"
    assert config.model.api_key is None
    assert config.searches[0] == Query("c#", ["bratislavsky-kraj"], 2, 2000, "m", 7)
    assert config.linkedin.searches[0] == LinkedInSearch("python developer in Slovakia", work_type="remote,hybrid")


# top-level count_days is the default, a search's own value wins
def test_count_days_default_and_override():
    config = parse_config({"count_days": 3, "searches": [{"keyword": "a"}, {"keyword": "b", "count_days": 7}]})
    assert [q.count_days for q in config.searches] == [3, 7]


# errors name the offending field
def test_invalid_config_names_the_field():
    with pytest.raises(ConfigError, match=r"searches\[0\].remote_work"):
        parse_config({"searches": [{"keyword": "c#", "remote_work": 5}]})


# CLI flags win over the config file
def test_cli_flags_override_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"pages": 2, "searches": [{"keyword": "python"}], "linkedin": {"searches": [{"keywords": "x"}]}}), encoding="utf-8")
    parser = build_parser()

    config = resolve_config(parser.parse_args(["c#", "--config", str(path), "--pages", "5", "--days", "1", "--interval", "15"]), parser)

    assert config.searches == [Query("c#", count_days=1)]
    assert config.linkedin is None
    assert config.pages == 5
    assert config.interval == 15
    assert config.db == tmp_path / "jobs.db"


# model.api_key is read from the config, empty means "use the env var", non-strings are rejected
def test_model_api_key():
    searches = [{"keyword": "a"}]
    assert parse_config({"searches": searches, "model": {"api_key": "k"}}).model.api_key == "k"
    assert parse_config({"searches": searches, "model": {"api_key": ""}}).model.api_key is None
    with pytest.raises(ConfigError, match="model.api_key"):
        parse_config({"searches": searches, "model": {"api_key": 1}})


# linkedin is off without its section; defaults fill in; bad values name the field
def test_linkedin_section():
    searches = [{"keyword": "a"}]
    assert parse_config({"searches": searches}).linkedin is None
    assert parse_config({"searches": searches, "linkedin": {"searches": [{"keywords": "dev"}]}}).linkedin == LinkedInConfig(
        [LinkedInSearch("dev")], "past_24_hours", 1
    )
    bad = [
        ({"searches": []}, "linkedin.searches"),
        ({"searches": [{"keywords": ""}]}, r"linkedin.searches\[0\].keywords"),
        ({"searches": [{"keywords": "a", "work_type": "remote,office"}]}, r"linkedin.searches\[0\].work_type"),
        ({"searches": [{"keywords": "a"}], "date_posted": "today"}, "linkedin.date_posted"),
        ({"searches": [{"keywords": "a"}], "max_pages": 11}, "linkedin.max_pages"),
        ({"searches": [{"keywords": "a"}], "location": "SK"}, "linkedin: unknown keys"),
    ]
    for linkedin, field in bad:
        with pytest.raises(ConfigError, match=field):
            parse_config({"searches": searches, "linkedin": linkedin})
