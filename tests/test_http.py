import pytest
import requests

from offer_monitor.http import retry_after_seconds


def response_with(headers: dict) -> requests.Response:
    resp = requests.Response()
    resp.headers.update(headers)
    return resp


@pytest.mark.parametrize(("value", "expected"), [("30", 30.0), ("0", 0.0), ("1.5", 1.5), ("-1", 0.0)])
def test_seconds_form(value, expected):
    assert retry_after_seconds(response_with({"Retry-After": value})) == expected


def test_header_name_case_does_not_matter():
    assert retry_after_seconds(response_with({"retry-after": "10"})) == 10.0


def test_negative_becomes_zero():
    assert retry_after_seconds(response_with({"Retry-After": "-5"})) == 0.0


# no header: None; Falls back to own delay;
def test_missing_header_is_none():
    assert retry_after_seconds(response_with({})) is None


# the HTTP-date form is not supported: None; Falls back to own delay;
def test_http_date_form_is_none():
    assert retry_after_seconds(response_with({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
