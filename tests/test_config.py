import shutil
from pathlib import Path

import pytest

from profesia_monitor.__main__ import build_parser, resolve_config
from profesia_monitor.config import load_config, parse_config as parse_main
from profesia_monitor.errors import ConfigError
from profesia_monitor.providers.linkedin.config import LinkedInConfig, LinkedInSearch, load_config as load_linkedin, parse_config as parse_linkedin
from profesia_monitor.providers.profesia_sk.config import Query, load_config as load_profesia, parse_config as parse_profesia

ROOT = Path(__file__).parents[1]
PKG = ROOT / "profesia_monitor" / "providers"


# the main example config is valid, and a run without provider files has both sources off
def test_main_example_loads_with_both_sources_off(tmp_path):
    shutil.copy(ROOT / "data" / "configs" / "config.example.yaml", tmp_path / "config.yaml")

    config = load_config(tmp_path / "config.yaml")

    # the example points at ../jobs.db, so the data folder is the copy's parent
    assert config.db.resolve() == (tmp_path / ".." / "jobs.db").resolve()
    assert config.backstory.resolve() == (tmp_path / ".." / "backstory.md").resolve()
    assert config.profesia is None
    assert config.linkedin is None
    assert config.model.api_key is None


# the provider examples load: their searches and filters are what the comments describe
def test_provider_examples_load():
    profesia = load_profesia(ROOT / "data" / "configs" / "profesia_sk.example.yaml")
    linkedin = load_linkedin(ROOT / "data" / "configs" / "linkedin.example.yaml")

    assert profesia.searches[0] == Query(keyword="c#", segments=["bratislavsky-kraj"], remote_work=2, min_salary=2000, salary_period="m", count_days=7)
    assert linkedin.searches[0] == LinkedInSearch(keywords="python developer in Slovakia", work_type="remote,hybrid")


# a provider is on exactly when its file sits next to the main config
def test_provider_files_turn_sources_on(tmp_path):
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "profesia_sk.yaml").write_text("searches:\n  - keyword: c#\n", encoding="utf-8")

    config = load_config(tmp_path / "config.yaml")

    assert [q.keyword for q in config.profesia.searches] == ["c#"]
    assert config.linkedin is None


# top-level count_days is inherited by searches that don't set their own; an explicit null stays null
def test_count_days_default_and_override():
    config = parse_profesia({"count_days": 3, "searches": [{"keyword": "a"}, {"keyword": "b", "count_days": 7}, {"keyword": "c", "count_days": None}]})
    assert [q.count_days for q in config.searches] == [3, 7, None]


# errors name the file, the field path and the problem
def test_invalid_config_names_the_field():
    with pytest.raises(ConfigError, match=r"profesia_sk.yaml: searches\[0\].remote_work: Input should be 0, 1 or 2"):
        parse_profesia({"searches": [{"keyword": "c#", "remote_work": 5}]})


# each value rule is enforced: zero pages, a bad slug, a blank keyword
def test_value_rules_are_enforced():
    with pytest.raises(ConfigError, match="pages"):
        parse_profesia({"pages": 0, "searches": [{"keyword": "a"}]})
    with pytest.raises(ConfigError, match=r"searches\[0\].segments"):
        parse_profesia({"searches": [{"keyword": "a", "segments": ["Bad Slug"]}]})
    with pytest.raises(ConfigError, match=r"searches\[0\].keyword"):
        parse_profesia({"searches": [{"keyword": "   "}]})


# CLI flags win over the provider files
def test_cli_flags_override_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("{}\n", encoding="utf-8")
    (tmp_path / "profesia_sk.yaml").write_text("pages: 2\nsearches:\n  - keyword: python\n", encoding="utf-8")
    (tmp_path / "linkedin.yaml").write_text("searches:\n  - keywords: x\n", encoding="utf-8")
    parser = build_parser()

    config = resolve_config(parser.parse_args(["c#", "--config", str(path), "--pages", "5", "--days", "1", "--interval", "15"]), parser)

    assert config.profesia.searches == [Query(keyword="c#", count_days=1)]
    assert config.linkedin is None  # one-off searches are Profesia-only
    assert config.profesia.pages == 5
    assert config.interval == 15
    assert config.db == tmp_path / "jobs.db"


# model.api_key is read from the main config, empty means "use the env var", non-strings are rejected
def test_model_api_key():
    assert parse_main({"model": {"api_key": "k"}}).model.api_key == "k"
    assert parse_main({"model": {"api_key": ""}}).model.api_key is None
    with pytest.raises(ConfigError, match="model.api_key"):
        parse_main({"model": {"api_key": 1}})


# source settings left in config.yaml get a message that says where they moved
def test_source_keys_in_main_config_are_rejected():
    with pytest.raises(ConfigError, match="moved out of this file"):
        parse_main({"searches": [{"keyword": "c#"}]})


# the LinkedIn file: defaults fill in, and bad values or unknown keys name the field
def test_linkedin_file():
    assert parse_linkedin({"searches": [{"keywords": "dev"}]}) == LinkedInConfig(searches=[LinkedInSearch(keywords="dev")])
    bad = [
        ({"searches": []}, "linkedin.yaml: searches: List should have at least 1 item"),
        ({"searches": [{"keywords": ""}]}, r"searches\[0\].keywords"),
        ({"searches": [{"keywords": "a", "work_type": "remote,office"}]}, r"searches\[0\].work_type"),
        ({"searches": [{"keywords": "a"}], "date_posted": "today"}, "date_posted"),
        ({"searches": [{"keywords": "a"}], "max_pages": 11}, "max_pages"),
        ({"searches": [{"keywords": "a"}], "location": "SK"}, "location"),
    ]
    for data, message in bad:
        with pytest.raises(ConfigError, match=message):
            parse_linkedin(data)


# a YAML syntax error names the file and the line
def test_yaml_syntax_error_names_the_line(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("db: jobs.db\ninterval: [60\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"config.yaml: invalid YAML \(line \d+\)"):
        load_config(path)
