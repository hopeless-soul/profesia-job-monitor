from pathlib import Path

import pytest

from offer_monitor.providers.profesia_sk.errors import ProfesiaLayoutError
from offer_monitor.providers.profesia_sk.parser import parse_listing

PAGE = (Path(__file__).parent / "fixtures" / "listing_page1.html").read_text(encoding="utf-8")


# every parsed offer carries the source the caller passed in
def test_parse_listing_sets_the_given_source():
    jobs, _ = parse_listing(PAGE, "other")

    assert jobs
    assert {j.source for j in jobs} == {"other"}


# rows on the page but none parse: the layout changed, which is an error and not an empty result
def test_changed_markup_is_a_layout_error():
    with pytest.raises(ProfesiaLayoutError):
        parse_listing('<ul><li class="list-row"><span>new markup</span></li></ul>', "profesia")


# a page with no rows at all is an empty search, not a failure
def test_empty_page_is_not_an_error():
    assert parse_listing("<ul></ul>", "profesia") == ([], False)
