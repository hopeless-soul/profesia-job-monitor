from pathlib import Path

from profesia_monitor.providers.profesia_sk.parser import parse_listing

PAGE = (Path(__file__).parent / "fixtures" / "listing_page1.html").read_text(encoding="utf-8")


# every parsed offer carries the source the caller passed in
def test_parse_listing_sets_the_given_source():
    jobs, _ = parse_listing(PAGE, "profesia")

    assert jobs
    assert {j.source for j in jobs} == {"profesia"}

    other, _ = parse_listing(PAGE, "other")
    assert {j.source for j in other} == {"other"}
