"""Regression tests for the local, dependency-free visual primitives."""

import pytest

from ui.theme import apply_app_theme, render_card, render_hero, render_status


class _MarkdownSink:
    def __init__(self):
        self.calls = []

    def markdown(self, value, **kwargs):
        self.calls.append((value, kwargs))


def test_app_theme_is_local_and_scoped():
    sink = _MarkdownSink()

    assert apply_app_theme(sink) is True

    markup, kwargs = sink.calls[0]
    assert 'id="idea-gold-design-system"' in markup
    assert '[data-testid="stAppViewContainer"]' in markup
    assert "http://" not in markup and "https://" not in markup
    assert kwargs == {"unsafe_allow_html": True}


def test_hero_and_card_escape_user_facing_copy():
    sink = _MarkdownSink()

    render_hero("Gold <script>", "A & B", meta=("<safe>",), st_module=sink)
    render_card("Review <now>", "One & two", st_module=sink)

    combined = "\n".join(call[0] for call in sink.calls)
    assert "<script>" not in combined
    assert "&lt;script&gt;" in combined
    assert "A &amp; B" in combined
    assert "&lt;safe&gt;" in combined
    assert "Review &lt;now&gt;" in combined


def test_error_status_has_accessible_role_and_invalid_tone_is_rejected():
    sink = _MarkdownSink()

    render_status("Fix it", tone="error", title="Blocked", st_module=sink)

    assert 'role="alert"' in sink.calls[0][0]
    assert 'aria-live="assertive"' in sink.calls[0][0]
    with pytest.raises(ValueError, match="unknown tone"):
        render_status("Nope", tone="loud", st_module=sink)
