"""Phase 9C — pure UI helpers for the ZerσSigma Algo Cockpit.

Stdlib-only, ZERO project imports and ZERO ``import streamlit`` so every helper is
trivially unit-testable. The Streamlit shell calls these to inject branded CSS and
render compact HTML cards / pills.

Palette is adapted (values only — no code copied) from the ZerσSigma Dashboard
theme: dark navy background, electric-green accent, cyan/blue accent.

NOTHING here executes, places, or previews an order. UI styling only.
"""

from __future__ import annotations

import html as _html
from typing import Any

# ── brand palette (hex/rgba values adapted from the Dashboard theme) ──────────
BRAND: dict[str, str] = {
    "bg": "#0b0f14",          # app background (dark navy/charcoal)
    "panel": "#141a22",       # card / panel surface
    "panel2": "#101317",      # secondary surface
    "text": "#e8e8e8",        # primary text
    "muted": "#a7b0bd",       # muted text
    "line": "#232a33",        # borders / grid lines
    "accent": "#00E5A8",      # electric green (primary brand accent)
    "accent2": "#19f5b2",     # green alt
    "blue": "#2d6cff",        # blue accent
    "danger": "#ff5c7a",      # warnings / stop
    "warn": "#ffcc66",        # amber
}


def brand_css() -> str:
    """Return a complete ``<style>`` block for the dark ZerσSigma cockpit.

    Targets stable Streamlit selectors (``.stApp``, ``[data-testid="stMetric"]``,
    ``.stTabs``) so it degrades gracefully across Streamlit versions."""
    b = BRAND
    return f"""
<style>
:root {{
  --zsa-bg: {b['bg']}; --zsa-panel: {b['panel']}; --zsa-panel2: {b['panel2']};
  --zsa-text: {b['text']}; --zsa-muted: {b['muted']}; --zsa-line: {b['line']};
  --zsa-accent: {b['accent']}; --zsa-blue: {b['blue']}; --zsa-danger: {b['danger']};
}}
.stApp {{
  background: radial-gradient(1200px 600px at 70% -10%, #11202b 0%, {b['bg']} 55%) fixed;
  color: {b['text']};
  font-family: "IBM Plex Sans", "Inter", -apple-system, Segoe UI, Roboto, sans-serif;
}}
h1, h2, h3, h4 {{ color: {b['text']}; letter-spacing: .2px; }}
a, a:visited {{ color: {b['accent']}; }}
code, pre, .stCode {{ font-family: "IBM Plex Mono", ui-monospace, Menlo, monospace; }}

/* tighter overall density (Phase 9D) */
.block-container {{ padding-top: 2.4rem; padding-bottom: 2rem; max-width: 1500px; }}
[data-testid="stVerticalBlock"] {{ gap: 0.5rem; }}
[data-testid="stHorizontalBlock"] {{ gap: 0.5rem; }}

/* compact native metric cards */
[data-testid="stMetric"] {{
  background: {b['panel']};
  border: 1px solid {b['line']};
  border-radius: 12px;
  padding: 7px 11px;
  box-shadow: 0 1px 0 rgba(255,255,255,.02) inset;
}}
[data-testid="stMetricLabel"] p {{ color: {b['muted']}; font-size: 11px; }}
[data-testid="stMetricValue"] {{ color: {b['text']}; font-weight: 800; font-size: 1.15rem; }}
[data-testid="stMetricDelta"] {{ font-size: 11px; }}

/* tabs: branded pill bar with a green active underline + subtle glow */
.stTabs [data-baseweb="tab-list"] {{
  gap: 4px; border-bottom: 1px solid {b['line']};
}}
.stTabs [data-baseweb="tab"] {{
  background: {b['panel2']}; border: 1px solid {b['line']};
  border-bottom: none; border-radius: 10px 10px 0 0;
  color: {b['muted']}; padding: 8px 16px;
}}
.stTabs [aria-selected="true"] {{
  color: {b['accent']} !important;
  border-color: {b['line']};
  box-shadow: 0 2px 10px rgba(0,229,168,.14);
}}

/* primary buttons → accent; default buttons → dark control */
.stButton > button {{
  border-radius: 12px; border: 1px solid {b['line']};
  background: rgba(20,30,48,.58); color: {b['text']};
}}
.stButton > button:hover {{ border-color: {b['blue']}; color: #fff; }}
.stButton > button[kind="primary"] {{
  background: {b['accent']}; color: #04110d; border: 1px solid {b['accent2']};
  font-weight: 700;
}}

/* dataframes + expanders blend into the dark theme */
[data-testid="stExpander"] {{ border: 1px solid {b['line']}; border-radius: 12px; }}
[data-testid="stDataFrame"] {{ border: 1px solid {b['line']}; border-radius: 12px; }}

/* custom cockpit primitives */
.zsa-hero {{
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 18px; margin-bottom: 6px;
  background: linear-gradient(90deg, {b['panel']} 0%, {b['panel2']} 100%);
  border: 1px solid {b['line']}; border-radius: 16px;
  box-shadow: 0 2px 18px rgba(0,229,168,.06);
}}
.zsa-hero-title {{ font-size: 20px; font-weight: 800; color: {b['text']}; }}
.zsa-hero-title .sig {{ color: {b['accent']}; }}
.zsa-hero-sub {{ color: {b['muted']}; font-size: 12px; margin-top: 2px; }}
.zsa-card {{
  background: {b['panel']}; border: 1px solid {b['line']};
  border-radius: 14px; padding: 14px 16px; margin-bottom: 10px;
}}
.zsa-metric {{ display: inline-block; min-width: 130px; }}
.zsa-metric .lbl {{ color: {b['muted']}; font-size: 12px; }}
.zsa-metric .val {{ color: {b['text']}; font-size: 22px; font-weight: 800; }}
.zsa-metric .sub {{ color: {b['muted']}; font-size: 12px; }}
.zsa-pill {{
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 12px; font-weight: 700; border: 1px solid {b['line']};
  background: {b['panel2']}; color: {b['muted']};
}}
.zsa-pill.green {{ color: #04110d; background: {b['accent']}; border-color: {b['accent2']}; }}
.zsa-pill.blue {{ color: #fff; background: {b['blue']}; border-color: rgba(101,152,255,.54); }}
.zsa-pill.red {{ color: #fff; background: {b['danger']}; border-color: {b['danger']}; }}
.zsa-pill.amber {{ color: #1a1406; background: {b['warn']}; border-color: {b['warn']}; }}
.zsa-pill.ghost {{ color: {b['muted']}; }}
</style>
"""


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


def pill(text: Any, kind: str = "ghost") -> str:
    """An inline pill badge. ``kind`` ∈ green|blue|red|amber|ghost."""
    kind = kind if kind in ("green", "blue", "red", "amber", "ghost") else "ghost"
    return f'<span class="zsa-pill {kind}">{_esc(text)}</span>'


def hero(title_html: str, subtitle: str = "", right_html: str = "") -> str:
    """The top command-center banner. ``title_html`` may contain the
    ``<span class="sig">σ</span>`` brand markup (already trusted)."""
    return (
        '<div class="zsa-hero"><div>'
        f'<div class="zsa-hero-title">{title_html}</div>'
        f'<div class="zsa-hero-sub">{_esc(subtitle)}</div>'
        f'</div><div>{right_html}</div></div>'
    )


def brand_title(text: str = "ZerσSigma Algo Cockpit") -> str:
    """Render the title with the σ highlighted in accent green. Escapes the
    rest of the text so only the known σ is emphasized."""
    if "σ" in text:
        before, _, after = text.partition("σ")
        return f'{_esc(before)}<span class="sig">σ</span>{_esc(after)}'
    return _esc(text)


def metric_card(label: Any, value: Any, sub: Any = None) -> str:
    """A compact custom metric card (HTML string)."""
    sub_html = f'<div class="sub">{_esc(sub)}</div>' if sub not in (None, "") else ""
    return (
        '<div class="zsa-card zsa-metric">'
        f'<div class="lbl">{_esc(label)}</div>'
        f'<div class="val">{_esc(value)}</div>{sub_html}</div>'
    )


# ── tiny formatting helpers (used across the cockpit) ────────────────────────

def dash(v: Any) -> str:
    return "—" if v is None or v == "" else str(v)


def fmt_money(v: Any, decimals: int = 2) -> str:
    try:
        return f"${float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def fmt_num(v: Any, decimals: int = 2) -> str:
    try:
        return f"{float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def pnl_kind(v: Any) -> str:
    """Pill kind for a P&L number: green ≥ 0, red < 0, ghost if unknown."""
    try:
        return "green" if float(v) >= 0 else "red"
    except (TypeError, ValueError):
        return "ghost"
