from offer_monitor.text import shorten


# Text within the limit comes back unchanged
def test_short_text_unchanged():
    assert shorten("Python developer", 120) == "Python developer"


# Text of exactly the limit length is not cut
def test_exact_limit_unchanged():
    text = "x" * 120
    assert shorten(text, 120) == text


# Longer text is cut to exactly the limit and ends with "..."
def test_long_text_cut_to_limit():
    result = shorten("a" * 300, 120)
    assert len(result) == 120
    assert result.endswith("...")
