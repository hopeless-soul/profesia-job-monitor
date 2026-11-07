import shutil
from pathlib import Path

import pytest

from offer_monitor.config import load_config, parse_config as parse_main
from offer_monitor.errors import ConfigError
from offer_monitor.providers.linkedin.config import LinkedInConfig, LinkedInSearch, load_config as load_linkedin, parse_config as parse_linkedin
from offer_monitor.providers.profesia_sk.config import Query, load_config as load_profesia, parse_config as parse_profesia

ROOT = Path(__file__).parents[1]


# the main example config is valid and lists only the Profesia source
def test_main_example_loads(tmp_path):
    shutil.copy(ROOT / "data" / "configs" / "config.example.yaml", tmp_path / "config.yaml")

    config = load_config(tmp_path / "config.yaml")

    # the example points at ../jobs.db, so the data folder is the copy's parent
    assert config.db.resolve() == (tmp_path / ".." / "jobs.db").resolve()
    assert config.backstory.resolve() == (tmp_path / ".." / "agent_context" / "backstory.md").resolve()
    assert config.providers == ["profesia"]
    assert config.model.api_key is None


# the provider examples load: their searches and filters are what the comments describe
def test_provider_examples_load():
    profesia = load_profesia(ROOT / "data" / "configs" / "profesia_sk.example.yaml")
    linkedin = load_linkedin(ROOT / "data" / "configs" / "linkedin.example.yaml")

    assert profesia.searches[0] == Query(keyword="c#", segments=["bratislavsky-kraj"], remote_work=2, min_salary=2000, salary_period="m", count_days=7)
    assert linkedin.searches[0] == LinkedInSearch(keywords="python developer in Slovakia", work_type="remote,hybrid")


# the list is required and holds known names only
def test_providers_list_errors():
    with pytest.raises(ConfigError, match="config.yaml: providers: list the sources to run"):
        parse_main({})
    with pytest.raises(ConfigError, match=r"providers\[0\]"):
        parse_main({"providers": ["indeed"]})


# provider settings belong in the provider's own file; an inline block in config.yaml is rejected
def test_inline_provider_block_is_rejected():
    with pytest.raises(ConfigError, match="profesia"):
        parse_main({"providers": [], "profesia": {"searches": [{"keyword": "c#"}]}})


# top-level count_days is inherited by searches that don't set their own; an explicit null stays null
def test_count_days_default_and_override():
    config = parse_profesia({"count_days": 3, "searches": [{"keyword": "a"}, {"keyword": "b", "count_days": 7}, {"keyword": "c", "count_days": None}]})
    assert [q.count_days for q in config.searches] == [3, 7, None]


# the Profesia file: errors name the file, the field path and the problem for each value rule
def test_profesia_file_rejects_bad_values():
    bad = [
        ({"searches": [{"keyword": "c#", "remote_work": 5}]}, r"profesia_sk.yaml: searches\[0\].remote_work: Input should be 0, 1 or 2"),
        ({"pages": 0, "searches": [{"keyword": "a"}]}, "pages"),
        ({"searches": [{"keyword": "a", "segments": ["Bad Slug"]}]}, r"searches\[0\].segments"),
        ({"searches": [{"keyword": "   "}]}, r"searches\[0\].keyword"),
    ]
    for data, message in bad:
        with pytest.raises(ConfigError, match=message):
            parse_profesia(data)


# model.api_key is read from the main config, empty means "use the env var", non-strings are rejected
def test_model_api_key():
    assert parse_main({"providers": [], "model": {"api_key": "k"}}).model.api_key == "k"
    assert parse_main({"providers": [], "model": {"api_key": ""}}).model.api_key is None
    with pytest.raises(ConfigError, match="model.api_key"):
        parse_main({"providers": [], "model": {"api_key": 1}})


# the LinkedIn file: defaults fill in, and bad values or unknown keys name the field
def test_linkedin_file():
    assert parse_linkedin({"searches": [{"keywords": "dev"}]}) == LinkedInConfig(searches=[LinkedInSearch(keywords="dev")])
    bad = [
        ({"searches": []}, "linkedin.yaml: searches: List should have at least 1 item"),
        ({"searches": [{"keywords": ""}]}, r"searches\[0\].keywords"),
        ({"searches": [{"keywords": "a", "work_type": "remote,office"}]}, r"searches\[0\].work_type"),
        ({"searches": [{"keywords": "a"}], "date_posted": "today"}, "date_posted"),
        ({"searches": [{"keywords": "a"}], "max_pages": 11}, "max_pages"),
        ({"searches": [{"keywords": "a"}], "delay": -1}, "delay"),
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
