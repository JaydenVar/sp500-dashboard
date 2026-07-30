"""Validated palette slots (see the dataviz skill's palette.md) split by light/dark mode.

Only documented hex values are used — nothing here is eyeballed. Blue/red is the
documented diverging pair (also categorical slots 1 and 8); a single-series chart
always gets slot-1 blue since that's an identity-neutral "the data" hue, not a
choice among several series.
"""

PALETTES = {
    "Light": dict(
        mode="light",
        surface="#fcfcfb",
        page="#f9f9f7",
        text_primary="#0b0b0b",
        text_secondary="#52514e",
        muted="#898781",
        gridline="#e1e0d9",
        baseline="#c3c2b7",
        border="rgba(11,11,11,0.10)",
        blue="#2a78d6",
        red="#e34948",
        neutral_mid="#f0efec",
        good_text="#006300",
        bad_text="#d03b3b",
        plotly_template="plotly_white",
    ),
    "Dark": dict(
        mode="dark",
        surface="#1a1a19",
        page="#0d0d0d",
        text_primary="#ffffff",
        text_secondary="#c3c2b7",
        muted="#898781",
        gridline="#2c2c2a",
        baseline="#383835",
        border="rgba(255,255,255,0.10)",
        blue="#3987e5",
        red="#e66767",
        neutral_mid="#383835",
        good_text="#0ca30c",
        bad_text="#e66767",
        plotly_template="plotly_dark",
    ),
}


def inject_css(pal: dict) -> str:
    return f"""
    <style>
    .stApp {{
        background-color: {pal['page']};
        color: {pal['text_primary']};
    }}
    section.main > div {{
        background-color: {pal['page']};
    }}
    [data-testid="stMetric"], .sp500-card {{
        background-color: {pal['surface']};
        border: 1px solid {pal['border']};
        border-radius: 8px;
        padding: 14px 16px;
    }}
    [data-testid="stMetricLabel"] {{
        color: {pal['text_secondary']} !important;
    }}
    [data-testid="stMetricValue"] {{
        color: {pal['text_primary']} !important;
        font-variant-numeric: proportional-nums;
    }}
    h1, h2, h3, h4, p, span, label, .stMarkdown {{
        color: {pal['text_primary']};
    }}
    .sp500-caption {{
        color: {pal['muted']};
        font-size: 0.85rem;
    }}
    [data-testid="stTabs"] button {{
        color: {pal['text_secondary']};
    }}
    </style>
    """
