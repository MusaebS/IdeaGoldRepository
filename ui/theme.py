"""Accessible, dependency-free visual primitives for the Streamlit app.

The module deliberately imports Streamlit lazily.  Pure/unit-test environments
can therefore import :mod:`ui.theme` without installing Streamlit, while the
render helpers can also receive a small ``st_module`` test double explicitly.

Call :func:`apply_app_theme` once per Streamlit script run, immediately after
``st.set_page_config``.  The remaining helpers render escaped, semantic HTML;
their text parameters are plain text rather than arbitrary HTML.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from hashlib import sha1
import html
import re
from typing import Any, Iterator, Literal, Sequence

__all__ = [
    "Tone",
    "apply_app_theme",
    "card_container",
    "render_card",
    "render_hero",
    "render_section_header",
    "render_status",
]

Tone = Literal["neutral", "info", "success", "warning", "error"]


# No external fonts, images, scripts, or stylesheets: deployments remain fully
# self-contained and do not leak page visits to third-party asset providers.
_APP_CSS = r"""
<style id="idea-gold-design-system">
  :root {
    --ig-canvas: #f7f6f2;
    --ig-surface: #ffffff;
    --ig-surface-subtle: #f1efe8;
    --ig-ink: #172033;
    --ig-muted: #5d6675;
    --ig-line: #dedbd2;
    --ig-brand: #7a5800;
    --ig-brand-strong: #5c4200;
    --ig-gold: #c7961e;
    --ig-gold-soft: #fbf3dc;
    --ig-navy: #14213d;
    --ig-info: #235b9f;
    --ig-info-soft: #edf5ff;
    --ig-success: #176b45;
    --ig-success-soft: #eaf7f0;
    --ig-warning: #8a5b00;
    --ig-warning-soft: #fff5da;
    --ig-error: #a62b25;
    --ig-error-soft: #fff0ee;
    --ig-focus: #175cd3;
    --ig-shadow-sm: 0 1px 2px rgba(20, 33, 61, 0.07);
    --ig-shadow-md: 0 10px 30px rgba(20, 33, 61, 0.10);
    --ig-radius-sm: 0.55rem;
    --ig-radius-md: 0.85rem;
    --ig-radius-lg: 1.15rem;
  }

  /* Scope application rules to Streamlit's stable root test id. */
  [data-testid="stAppViewContainer"] {
    color: var(--ig-ink);
    background:
      radial-gradient(circle at 88% 0%, rgba(199, 150, 30, 0.10), transparent 27rem),
      var(--ig-canvas);
  }

  [data-testid="stAppViewContainer"] .stMainBlockContainer,
  [data-testid="stAppViewContainer"] .block-container {
    max-width: 92rem;
    padding-top: 2rem;
    padding-bottom: 4rem;
  }

  [data-testid="stHeader"] {
    background: rgba(247, 246, 242, 0.88);
    border-bottom: 1px solid rgba(222, 219, 210, 0.75);
    backdrop-filter: blur(10px);
  }

  [data-testid="stSidebar"] {
    border-right: 1px solid var(--ig-line);
    background: #efede6;
  }

  [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
    padding-top: 1.25rem;
  }

  [data-testid="stAppViewContainer"] h1,
  [data-testid="stAppViewContainer"] h2,
  [data-testid="stAppViewContainer"] h3 {
    color: var(--ig-navy);
    letter-spacing: -0.025em;
  }

  [data-testid="stAppViewContainer"] p,
  [data-testid="stAppViewContainer"] label {
    color: var(--ig-ink);
  }

  [data-testid="stCaptionContainer"] p {
    color: var(--ig-muted);
  }

  [data-testid="stAppViewContainer"] hr {
    border-color: var(--ig-line);
    margin-block: 1.4rem;
  }

  /* Controls use test ids or Base Web roles, avoiding generated class names. */
  [data-testid="stWidgetLabel"] p {
    color: var(--ig-ink);
    font-weight: 650;
  }

  [data-testid="stAppViewContainer"] [data-baseweb="input"],
  [data-testid="stAppViewContainer"] [data-baseweb="select"] > div,
  [data-testid="stAppViewContainer"] textarea {
    border-color: #c9c5bb;
    border-radius: var(--ig-radius-sm);
    background: var(--ig-surface);
  }

  [data-testid="stAppViewContainer"] [data-baseweb="input"]:focus-within,
  [data-testid="stAppViewContainer"] [data-baseweb="select"] > div:focus-within,
  [data-testid="stAppViewContainer"] textarea:focus {
    border-color: var(--ig-focus);
    box-shadow: 0 0 0 2px rgba(23, 92, 211, 0.16);
  }

  [data-testid="stButton"] > button,
  [data-testid="stDownloadButton"] > button,
  [data-testid="stFormSubmitButton"] > button {
    min-height: 2.65rem;
    border: 1px solid #bcb7aa;
    border-radius: var(--ig-radius-sm);
    color: var(--ig-navy);
    background: var(--ig-surface);
    font-weight: 700;
    box-shadow: var(--ig-shadow-sm);
    transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
  }

  [data-testid="stButton"] > button:hover,
  [data-testid="stDownloadButton"] > button:hover,
  [data-testid="stFormSubmitButton"] > button:hover {
    border-color: var(--ig-brand);
    color: var(--ig-brand-strong);
    box-shadow: 0 4px 12px rgba(20, 33, 61, 0.10);
    transform: translateY(-1px);
  }

  [data-testid="stBaseButton-primary"] {
    border-color: var(--ig-brand-strong) !important;
    color: #ffffff !important;
    background: var(--ig-brand-strong) !important;
  }

  [data-testid="stBaseButton-primary"]:hover {
    border-color: var(--ig-navy) !important;
    background: var(--ig-navy) !important;
  }

  [data-testid="stAppViewContainer"]
    :where(button, a, input, textarea, select, [role="button"], [tabindex]):focus-visible {
    outline: 3px solid var(--ig-focus) !important;
    outline-offset: 2px !important;
  }

  /* Dense data-entry surfaces remain calm and visually grouped. */
  [data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap: 0.35rem;
    border-bottom: 1px solid var(--ig-line);
  }

  [data-testid="stTabs"] [data-baseweb="tab"] {
    min-height: 3rem;
    padding-inline: 1rem;
    border-radius: var(--ig-radius-sm) var(--ig-radius-sm) 0 0;
    color: var(--ig-muted);
    font-weight: 700;
  }

  [data-testid="stTabs"] [aria-selected="true"] {
    color: var(--ig-brand-strong);
    background: var(--ig-gold-soft);
  }

  [data-testid="stExpander"] details,
  [data-testid="stForm"],
  [data-testid="stVerticalBlockBorderWrapper"] {
    border-color: var(--ig-line) !important;
    border-radius: var(--ig-radius-md) !important;
    background: rgba(255, 255, 255, 0.84);
    box-shadow: var(--ig-shadow-sm);
  }

  [data-testid="stExpander"] summary {
    color: var(--ig-navy);
    font-weight: 700;
  }

  [data-testid="stDataFrame"],
  [data-testid="stDataEditor"] {
    overflow: hidden;
    border: 1px solid var(--ig-line);
    border-radius: var(--ig-radius-md);
    background: var(--ig-surface);
    box-shadow: var(--ig-shadow-sm);
  }

  [data-testid="stFileUploaderDropzone"] {
    border: 1.5px dashed #aaa393;
    border-radius: var(--ig-radius-md);
    background: #faf9f5;
  }

  [data-testid="stMetric"] {
    min-height: 7.35rem;
    padding: 1rem 1.05rem;
    border: 1px solid var(--ig-line);
    border-top: 3px solid var(--ig-gold);
    border-radius: var(--ig-radius-md);
    background: var(--ig-surface);
    box-shadow: var(--ig-shadow-sm);
  }

  [data-testid="stMetricLabel"] p {
    color: var(--ig-muted);
    font-size: 0.78rem;
    font-weight: 750;
    letter-spacing: 0.055em;
    text-transform: uppercase;
  }

  [data-testid="stMetricValue"] {
    color: var(--ig-navy);
  }

  [data-testid="stAlert"] {
    border-radius: var(--ig-radius-md);
    box-shadow: var(--ig-shadow-sm);
  }

  /* Semantic primitives rendered by this module. */
  .ig-hero {
    position: relative;
    overflow: hidden;
    margin: 0 0 1.65rem;
    padding: clamp(1.45rem, 3vw, 2.5rem);
    border: 1px solid rgba(199, 150, 30, 0.36);
    border-radius: var(--ig-radius-lg);
    color: #ffffff;
    background:
      linear-gradient(120deg, rgba(255, 255, 255, 0.07), transparent 42%),
      var(--ig-navy);
    box-shadow: var(--ig-shadow-md);
  }

  .ig-hero::after {
    position: absolute;
    inset: 0 0 auto;
    height: 0.28rem;
    background: linear-gradient(90deg, var(--ig-gold), #f0d27c, var(--ig-gold));
    content: "";
  }

  .ig-hero__eyebrow,
  .ig-section-heading__eyebrow,
  .ig-card__eyebrow {
    margin: 0 0 0.45rem;
    color: var(--ig-brand) !important;
    font-size: 0.76rem;
    font-weight: 800;
    letter-spacing: 0.105em;
    text-transform: uppercase;
  }

  .ig-hero__eyebrow {
    color: #f0d27c !important;
  }

  .ig-hero__title {
    max-width: 55rem;
    margin: 0;
    color: #ffffff !important;
    font-size: clamp(2rem, 4.8vw, 3.65rem);
    line-height: 1.02;
    letter-spacing: -0.045em;
  }

  .ig-hero__subtitle {
    max-width: 55rem;
    margin: 0.9rem 0 0;
    color: #e8ecf3 !important;
    font-size: clamp(1rem, 1.8vw, 1.16rem);
    line-height: 1.65;
  }

  .ig-hero__meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.55rem;
    margin: 1.15rem 0 0;
    padding: 0;
    list-style: none;
  }

  .ig-hero__meta li {
    padding: 0.35rem 0.65rem;
    border: 1px solid rgba(255, 255, 255, 0.22);
    border-radius: 999px;
    color: #ffffff;
    background: rgba(255, 255, 255, 0.08);
    font-size: 0.82rem;
    font-weight: 650;
  }

  .ig-section-heading {
    margin: 1.9rem 0 0.9rem;
    padding-left: 0.95rem;
    border-left: 0.28rem solid var(--ig-gold);
  }

  .ig-section-heading h2,
  .ig-section-heading h3 {
    margin: 0;
    color: var(--ig-navy);
    line-height: 1.2;
  }

  .ig-section-heading__description {
    max-width: 70rem;
    margin: 0.38rem 0 0;
    color: var(--ig-muted) !important;
    line-height: 1.55;
  }

  .ig-status {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 0.75rem;
    align-items: start;
    margin: 0.75rem 0;
    padding: 0.9rem 1rem;
    border: 1px solid var(--ig-line);
    border-left-width: 0.3rem;
    border-radius: var(--ig-radius-md);
    background: var(--ig-surface-subtle);
  }

  .ig-status__label {
    min-width: 4.75rem;
    padding-top: 0.06rem;
    color: currentColor;
    font-size: 0.76rem;
    font-weight: 850;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .ig-status__title {
    display: block;
    margin-bottom: 0.12rem;
    color: currentColor;
    font-weight: 800;
  }

  .ig-status__message {
    color: var(--ig-ink);
    line-height: 1.5;
  }

  .ig-status--info {
    border-left-color: var(--ig-info);
    color: var(--ig-info);
    background: var(--ig-info-soft);
  }

  .ig-status--success {
    border-left-color: var(--ig-success);
    color: var(--ig-success);
    background: var(--ig-success-soft);
  }

  .ig-status--warning {
    border-left-color: var(--ig-warning);
    color: var(--ig-warning);
    background: var(--ig-warning-soft);
  }

  .ig-status--error {
    border-left-color: var(--ig-error);
    color: var(--ig-error);
    background: var(--ig-error-soft);
  }

  .ig-card {
    height: 100%;
    padding: 1.1rem 1.15rem;
    border: 1px solid var(--ig-line);
    border-top: 3px solid var(--ig-gold);
    border-radius: var(--ig-radius-md);
    background: var(--ig-surface);
    box-shadow: var(--ig-shadow-sm);
  }

  .ig-card--info { border-top-color: var(--ig-info); }
  .ig-card--success { border-top-color: var(--ig-success); }
  .ig-card--warning { border-top-color: var(--ig-warning); }
  .ig-card--error { border-top-color: var(--ig-error); }

  .ig-card__title {
    margin: 0;
    color: var(--ig-navy);
    font-size: 1.05rem;
    line-height: 1.3;
  }

  .ig-card__body {
    margin: 0.55rem 0 0;
    color: var(--ig-muted) !important;
    line-height: 1.55;
  }

  .ig-card__footer {
    margin: 0.85rem 0 0;
    padding-top: 0.7rem;
    border-top: 1px solid var(--ig-line);
    color: var(--ig-muted) !important;
    font-size: 0.84rem;
  }

  @media (max-width: 48rem) {
    [data-testid="stAppViewContainer"] .stMainBlockContainer,
    [data-testid="stAppViewContainer"] .block-container {
      padding-top: 1.2rem;
      padding-inline: 1rem;
    }

    .ig-hero { border-radius: var(--ig-radius-md); }
    .ig-status { grid-template-columns: 1fr; gap: 0.25rem; }
  }

  @media (prefers-reduced-motion: reduce) {
    [data-testid="stAppViewContainer"] *,
    [data-testid="stAppViewContainer"] *::before,
    [data-testid="stAppViewContainer"] *::after {
      scroll-behavior: auto !important;
      transition-duration: 0.01ms !important;
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
    }
  }
</style>
"""

_TONE_LABELS: dict[Tone, str] = {
    "neutral": "Note",
    "info": "Info",
    "success": "Success",
    "warning": "Warning",
    "error": "Error",
}


def _streamlit(st_module: Any | None = None) -> Any | None:
    """Return Streamlit (or an injected double) without an import-time dependency."""
    if st_module is not None:
        return st_module
    try:
        import streamlit as st  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError):
        return None
    return st


def _markdown(markup: str, st_module: Any | None = None) -> bool:
    """Render trusted module-owned markup; tolerate minimal test doubles."""
    st = _streamlit(st_module)
    markdown = getattr(st, "markdown", None) if st is not None else None
    if not callable(markdown):
        return False
    try:
        markdown(markup, unsafe_allow_html=True)
    except TypeError:
        # A lightweight stub may implement ``markdown(value)`` only.  User
        # content is still escaped before this function receives the markup.
        markdown(markup)
    return True


def _escaped(value: Any) -> str:
    """Escape plain text for HTML and retain intentional line breaks."""
    return html.escape(str(value), quote=True).replace("\n", "<br>")


def _element_id(value: str, prefix: str) -> str:
    """Return a deterministic, HTML-safe id for accessible label wiring."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        digest = sha1(value.encode("utf-8")).hexdigest()[:8]
        slug = f"item-{digest}"
    return f"ig-{prefix}-{slug}"


def _tone(value: str) -> Tone:
    if value not in _TONE_LABELS:
        choices = ", ".join(_TONE_LABELS)
        raise ValueError(f"unknown tone {value!r}; expected one of: {choices}")
    return value  # type: ignore[return-value]


def apply_app_theme(st_module: Any | None = None) -> bool:
    """Inject the local design system for the current Streamlit script run.

    Returns ``True`` when CSS was rendered.  In an environment without
    Streamlit (or with a stub that has no ``markdown`` function), it safely
    returns ``False`` instead of making module imports fail.
    """
    return _markdown(_APP_CSS, st_module)


def render_hero(
    title: str,
    subtitle: str | None = None,
    *,
    eyebrow: str | None = "Idea Gold Scheduler",
    meta: Sequence[str] = (),
    st_module: Any | None = None,
) -> bool:
    """Render the page's semantic ``h1`` hero with optional metadata chips."""
    eyebrow_html = f'<p class="ig-hero__eyebrow">{_escaped(eyebrow)}</p>' if eyebrow else ""
    subtitle_html = f'<p class="ig-hero__subtitle">{_escaped(subtitle)}</p>' if subtitle else ""
    meta_html = ""
    if meta:
        items = "".join(f"<li>{_escaped(item)}</li>" for item in meta)
        meta_html = f'<ul class="ig-hero__meta" aria-label="Highlights">{items}</ul>'
    markup = f"""
<header class="ig-hero">
  {eyebrow_html}
  <h1 class="ig-hero__title">{_escaped(title)}</h1>
  {subtitle_html}
  {meta_html}
</header>
"""
    return _markdown(markup, st_module)


def render_section_header(
    title: str,
    description: str | None = None,
    *,
    eyebrow: str | None = None,
    anchor: str | None = None,
    level: Literal[2, 3] = 2,
    st_module: Any | None = None,
) -> bool:
    """Render an accessible section heading with a stable link target."""
    heading_id = _element_id(anchor or title, "section")
    eyebrow_html = (
        f'<p class="ig-section-heading__eyebrow">{_escaped(eyebrow)}</p>' if eyebrow else ""
    )
    description_html = (
        f'<p class="ig-section-heading__description">{_escaped(description)}</p>'
        if description
        else ""
    )
    markup = f"""
<section class="ig-section-heading" aria-labelledby="{heading_id}">
  {eyebrow_html}
  <h{level} id="{heading_id}">{_escaped(title)}</h{level}>
  {description_html}
</section>
"""
    return _markdown(markup, st_module)


def render_status(
    message: str,
    *,
    tone: Tone = "info",
    title: str | None = None,
    label: str | None = None,
    st_module: Any | None = None,
) -> bool:
    """Render a labelled status message that does not rely on colour alone."""
    resolved_tone = _tone(tone)
    visible_label = label or _TONE_LABELS[resolved_tone]
    title_html = f'<span class="ig-status__title">{_escaped(title)}</span>' if title else ""
    role = "alert" if resolved_tone in {"warning", "error"} else "status"
    live = "assertive" if resolved_tone == "error" else "polite"
    markup = f"""
<aside class="ig-status ig-status--{resolved_tone}" role="{role}" aria-live="{live}">
  <span class="ig-status__label">{_escaped(visible_label)}</span>
  <span class="ig-status__message">{title_html}{_escaped(message)}</span>
</aside>
"""
    return _markdown(markup, st_module)


def render_card(
    title: str,
    body: str | None = None,
    *,
    eyebrow: str | None = None,
    footer: str | None = None,
    tone: Tone = "neutral",
    st_module: Any | None = None,
) -> bool:
    """Render a self-contained informational card from escaped plain text."""
    resolved_tone = _tone(tone)
    heading_id = _element_id(title, "card")
    eyebrow_html = f'<p class="ig-card__eyebrow">{_escaped(eyebrow)}</p>' if eyebrow else ""
    body_html = f'<p class="ig-card__body">{_escaped(body)}</p>' if body else ""
    footer_html = f'<p class="ig-card__footer">{_escaped(footer)}</p>' if footer else ""
    markup = f"""
<article class="ig-card ig-card--{resolved_tone}" aria-labelledby="{heading_id}">
  {eyebrow_html}
  <h3 class="ig-card__title" id="{heading_id}">{_escaped(title)}</h3>
  {body_html}
  {footer_html}
</article>
"""
    return _markdown(markup, st_module)


@contextmanager
def card_container(
    title: str | None = None,
    description: str | None = None,
    *,
    st_module: Any | None = None,
) -> Iterator[Any | None]:
    """Yield a themed bordered Streamlit container suitable for live widgets.

    ``render_card`` is preferable for static copy.  This context manager is for
    cards containing native Streamlit controls.  It degrades to a no-op context
    when Streamlit (or ``st.container``) is unavailable, which keeps headless
    imports and simple unit-test stubs safe.
    """
    st = _streamlit(st_module)
    container_factory = getattr(st, "container", None) if st is not None else None
    if not callable(container_factory):
        yield None
        return

    try:
        container = container_factory(border=True)
    except TypeError:
        # Streamlit before bordered containers, or a minimal test double.
        container = container_factory()

    manager = container if hasattr(container, "__enter__") else nullcontext(container)
    with manager:
        if title:
            render_section_header(
                title,
                description,
                level=3,
                st_module=st,
            )
        elif description:
            caption = getattr(st, "caption", None)
            if callable(caption):
                caption(description)
        yield container
