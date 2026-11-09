import pytest

from offer_monitor.text import shorten


def test_at_default_limit_is_unchanged():
    default_limit = 32
    text = "x" * default_limit
    assert shorten(text) == text


def test_over_default_limit_is_cut_to_limit():
    default_limit = 32
    text = "x" * 50
    result = shorten(text)
    assert result == "x" * (default_limit - 3) + "..."


def test_at_custom_limit():
    text = "loremipsum"
    result = shorten(text, 10)
    assert result == "loremipsum"


def test_over_custom_limit_is_cut_to_limit():
    result = shorten("x" * 50, 10)
    assert result == "x" * 7 + "..."
    assert len(result) == 10


# the same rule checked on several lengths at once: the result never goes past the limit
@pytest.mark.parametrize("length", [0, 1, 9, 10, 11, 100])
def test_never_longer_than_limit(length):
    assert len(shorten("x" * length, 10)) <= 10
