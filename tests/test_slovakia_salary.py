# Lesson 1: tests for parse_salary, which turns a Profesia salary label into a Salary

import pytest

from offer_monitor.models import Salary
from offer_monitor.providers.profesia_sk.parser import parse_salary


def test_range():
    assert parse_salary("1 500 - 2 000 EUR/mesiac") == Salary(
        raw="1 500 - 2 000 EUR/mesiac", min=1500, max=2000, currency="EUR", period="month"
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("1 500 - 2 000 EUR/mesiac", (1500, 2000, "EUR", "month")),
        ("2500 CZK/mesiac", (2500, 2500, "CZK", "month")),
        ("Od 3000 EUR/mesiac", (3000, None, "EUR", "month")),
        ("lorem", (None, None, None, None)),
        ("1200 - 1500 CZK per month", (1200, 1500, "CZK", "month")),
        ("25 USD/hour", (25, 25, "USD", "hour")),
        ("From 10 EUR/hour", (10, None, "EUR", "hour")),
        ("Do 2000 CZK/rok", (None, 2000, "CZK", "year")),
        ("1 500 - 2 000 EUR per year", (1500, 2000, "EUR", "year")),
    ],
)
def test_salary_fields(label, expected):
    s = parse_salary(label)
    assert (s.min, s.max, s.currency, s.period) == expected


# empty input gives no Salary at all
@pytest.mark.parametrize("label", [None, "", "   "])
def test_empty_label_is_none(label):
    assert parse_salary(label) is None
