"""Palette values and the app's CSS.

Colors are documented, validated values — categorical slots for series identity,
blue/red as the diverging pair, and a fixed status scale. Both modes are chosen
for their own surface rather than being an automatic inversion of each other.

Validated per mode (OKLCH lightness band, chroma floor, Machado-2009 protan/
deutan separation, normal-vision floor, WCAG contrast vs surface). The series
order is the colorblind-safety mechanism: assign slots in order, never cycle.
"""

from __future__ import annotations

# Categorical series slots, in fixed assignment order.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]

PALETTES = {
    "Light": dict(
        mode="light",
        surface="#ffffff",
        surface_2="#fbfbfa",
        page="#f4f5f7",
        text_primary="#0b0b0b",
        text_secondary="#52514e",
        muted="#898781",
        gridline="#ebebe6",
        baseline="#c3c2b7",
        border="rgba(11,11,11,0.10)",
        border_strong="rgba(11,11,11,0.16)",
        hover="rgba(42,120,214,0.06)",
        blue="#2a78d6",
        red="#e34948",
        neutral_mid="#f0efec",
        up="#0b7d3f",
        down="#c62828",
        up_bg="rgba(11,125,63,0.10)",
        down_bg="rgba(198,40,40,0.10)",
        accent="#2a78d6",
        series=SERIES_LIGHT,
        plotly_template="plotly_white",
        shadow="0 1px 2px rgba(11,11,11,0.04), 0 2px 8px rgba(11,11,11,0.04)",
        shadow_hover="0 2px 4px rgba(11,11,11,0.06), 0 8px 20px rgba(11,11,11,0.08)",
    ),
    "Dark": dict(
        mode="dark",
        surface="#1a1a19",
        surface_2="#212120",
        page="#0d0d0d",
        text_primary="#ffffff",
        text_secondary="#c3c2b7",
        muted="#898781",
        gridline="#2c2c2a",
        baseline="#383835",
        border="rgba(255,255,255,0.10)",
        border_strong="rgba(255,255,255,0.18)",
        hover="rgba(57,135,229,0.12)",
        blue="#3987e5",
        red="#e66767",
        neutral_mid="#383835",
        up="#3ec46b",
        down="#f2685f",
        up_bg="rgba(62,196,107,0.14)",
        down_bg="rgba(242,104,95,0.14)",
        accent="#3987e5",
        series=SERIES_DARK,
        plotly_template="plotly_dark",
        shadow="0 1px 2px rgba(0,0,0,0.4), 0 2px 8px rgba(0,0,0,0.3)",
        shadow_hover="0 2px 4px rgba(0,0,0,0.5), 0 8px 20px rgba(0,0,0,0.45)",
    ),
}

FONT_STACK = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
MONO_STACK = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace'


def app_css(p: dict) -> str:
    """The whole stylesheet. Written against the palette dict so a mode swap is
    one substitution and no rule hardcodes a hex."""
    return f"""
<style>
:root {{
  --surface: {p['surface']};
  --surface-2: {p['surface_2']};
  --page: {p['page']};
  --text-1: {p['text_primary']};
  --text-2: {p['text_secondary']};
  --muted: {p['muted']};
  --border: {p['border']};
  --border-strong: {p['border_strong']};
  --accent: {p['accent']};
  --up: {p['up']};
  --down: {p['down']};
  --shadow: {p['shadow']};
  --shadow-hover: {p['shadow_hover']};
  --radius: 10px;
}}

/* ---------- shell ---------- */
/* `color` here is load-bearing, not decoration: plain <div>s (the header title,
   card values) inherit it, and without it they keep Streamlit's own ink and go
   near-invisible in dark mode. */
.stApp {{ background: var(--page); color: var(--text-1); }}
html, body, [class*="css"] {{ font-family: {FONT_STACK}; }}
.block-container {{
  padding-top: 1.25rem !important;
  padding-bottom: 3rem !important;
  max-width: 1600px;
}}
#MainMenu, footer, header[data-testid="stHeader"] {{ visibility: hidden; height: 0; }}

h1, h2, h3, h4, h5 {{ color: var(--text-1); letter-spacing: -0.015em; }}
p, span, label, li {{ color: var(--text-1); }}
a {{ color: var(--accent); }}
hr {{ border-color: var(--border); margin: 1.25rem 0; }}

/* ---------- app header ---------- */
.hdr {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 20px; flex-wrap: wrap;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
  box-shadow: var(--shadow);
  margin-bottom: 14px;
}}
.hdr-left {{ display: flex; align-items: center; gap: 14px; min-width: 0; }}
.hdr-mark {{
  width: 38px; height: 38px; flex: 0 0 38px;
  border-radius: 9px;
  background: linear-gradient(135deg, {p['series'][0]}, {p['series'][6]});
  display: flex; align-items: center; justify-content: center;
  font-size: 19px;
}}
.hdr-title {{ font-size: 1.30rem; font-weight: 680; line-height: 1.15; letter-spacing: -0.02em; }}
.hdr-sub {{ font-size: 0.82rem; color: var(--text-2); margin-top: 2px; }}
.hdr-right {{ display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }}
.hdr-meta {{ font-size: 0.74rem; color: var(--muted); text-align: right; line-height: 1.45; }}
.hdr-meta b {{ color: var(--text-2); font-weight: 600; }}

/* market status pill */
.mkt {{
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 12px; border-radius: 999px;
  font-size: 0.75rem; font-weight: 600;
  border: 1px solid var(--border-strong);
  background: var(--surface-2);
  white-space: nowrap;
}}
.dot {{ width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }}
.dot-open {{ background: var(--up); box-shadow: 0 0 0 3px {p['up_bg']}; animation: pulse 2s ease-in-out infinite; }}
.dot-closed {{ background: var(--muted); }}
@keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.45; }} }}
@media (prefers-reduced-motion: reduce) {{ .dot-open {{ animation: none; }} }}

/* ---------- metric cards ---------- */
.kpi-grid {{
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  margin: 4px 0 6px;
}}
.kpi {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px 15px;
  box-shadow: var(--shadow);
  transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
}}
.kpi:hover {{ transform: translateY(-2px); box-shadow: var(--shadow-hover); border-color: var(--border-strong); }}
@media (prefers-reduced-motion: reduce) {{ .kpi:hover {{ transform: none; }} }}
.kpi-top {{ display: flex; align-items: center; gap: 7px; margin-bottom: 9px; }}
.kpi-ico {{ font-size: 0.86rem; opacity: .75; line-height: 1; }}
.kpi-label {{
  font-size: 0.685rem; font-weight: 640; color: var(--muted);
  text-transform: uppercase; letter-spacing: .07em;
}}
.kpi-val {{
  font-size: 1.72rem; font-weight: 660; line-height: 1.1;
  color: var(--text-1); letter-spacing: -0.025em;
}}
.kpi-val.sm {{ font-size: 1.30rem; }}
.kpi-foot {{ font-size: 0.735rem; color: var(--muted); margin-top: 6px; line-height: 1.35; }}
.chg {{
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 0.755rem; font-weight: 650;
  padding: 2px 7px; border-radius: 6px; margin-top: 7px;
}}
.chg-up {{ color: var(--up); background: {p['up_bg']}; }}
.chg-down {{ color: var(--down); background: {p['down_bg']}; }}
.chg-flat {{ color: var(--muted); background: var(--surface-2); }}

/* ---------- quote strip ---------- */
.quote {{
  display: flex; align-items: flex-end; gap: 16px; flex-wrap: wrap;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px 20px; box-shadow: var(--shadow);
  margin-bottom: 12px;
}}
.q-name {{ font-size: 1.06rem; font-weight: 660; letter-spacing: -0.015em; }}
.q-tkr {{
  font-family: {MONO_STACK}; font-size: 0.72rem; font-weight: 650;
  color: var(--text-2); background: var(--surface-2);
  border: 1px solid var(--border); border-radius: 5px;
  padding: 2px 7px; margin-left: 8px; vertical-align: 2px;
}}
.q-meta {{ font-size: 0.75rem; color: var(--muted); margin-top: 3px; }}
.q-price {{ font-size: 2.05rem; font-weight: 680; line-height: 1; letter-spacing: -0.03em; }}
.q-chg {{ font-size: 0.95rem; font-weight: 650; margin-left: 2px; }}

/* ---------- ask the market ---------- */
.answer {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 10px;
  padding: 15px 18px;
  margin: 4px 0 12px;
  font-size: 1.04rem; line-height: 1.5; color: var(--text-1);
  box-shadow: var(--shadow);
}}
.st-key-ask_q input {{ font-size: 1rem !important; padding: 10px 14px !important; }}

/* ---------- developer center ---------- */
.qbox {{
  background: linear-gradient(135deg, {p['hover']}, transparent 70%);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 10px;
  padding: 14px 18px;
  margin: 6px 0 14px;
}}
.qbox-k {{
  font-size: 0.66rem; font-weight: 680; color: var(--accent);
  text-transform: uppercase; letter-spacing: .09em; margin-bottom: 5px;
}}
.qbox-q {{ font-size: 1.08rem; font-weight: 620; color: var(--text-1); line-height: 1.4; }}
.uses {{
  display: inline-block;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 7px; padding: 6px 12px; margin: 0 7px 7px 0;
  font-size: 0.82rem; color: var(--text-1);
}}
.modepill {{
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 0.68rem; font-weight: 700; letter-spacing: .07em;
  text-transform: uppercase; padding: 4px 10px; border-radius: 999px;
  background: var(--surface-2); border: 1px solid var(--border-strong);
  color: var(--text-2);
}}
.modepill.dev {{ border-color: var(--accent); color: var(--accent); }}

/* ---------- section headings ---------- */
.sec {{ display: flex; align-items: baseline; gap: 9px; margin: 20px 0 4px; }}
.sec-t {{ font-size: 1.0rem; font-weight: 660; letter-spacing: -0.012em; color: var(--text-1); }}
.sec-s {{ font-size: 0.765rem; color: var(--muted); }}
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 6px 10px 2px;
  box-shadow: var(--shadow); margin-bottom: 6px;
}}
.note {{ font-size: 0.75rem; color: var(--muted); line-height: 1.5; }}

/* ---------- detail cards ----------
   One surface, one border, one spacing scale for every label/value panel in the
   app. These began life scoped to the Intelligence page, which is exactly why
   that page read tighter than the rest -- so they are global now and reached
   through `ui.card()` / `ui.metric_rows()`. Every color is a palette variable
   that already passed the contrast and color-vision validation. */
.mi-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: .85rem 1rem; margin-bottom: .6rem;
}}
.mi-card h4 {{ margin: 0 0 .35rem 0; font-size: .95rem; color: var(--text-1); }}
.mi-metric {{
  display: flex; justify-content: space-between; gap: 1rem;
  padding: .3rem 0; border-bottom: 1px solid var(--border); font-size: .9rem;
}}
.mi-metric:last-child {{ border-bottom: none; }}
.mi-metric .k {{ color: var(--text-2); }}
.mi-metric .v {{ color: var(--text-1); font-weight: 600; font-variant-numeric: tabular-nums; }}
.mi-pct {{ font-size: .78rem; color: var(--text-2); margin-left: .4rem; }}
.mi-bar {{
  height: 6px; border-radius: 3px; background: var(--surface-2);
  overflow: hidden; margin-top: .25rem;
}}
.mi-bar > span {{ display: block; height: 100%; border-radius: 3px; }}
.mi-chip {{
  display: inline-block; min-width: 2.4rem; text-align: center;
  padding: .12rem .45rem; border-radius: 6px; font-weight: 700; font-size: .86rem;
}}
.mi-news {{ padding: .45rem 0; border-bottom: 1px solid var(--border); }}
.mi-news:last-child {{ border-bottom: none; }}
.mi-news a {{ color: var(--text-1); text-decoration: none; font-weight: 600; font-size: .9rem; }}
.mi-news a:hover {{ color: var(--accent); text-decoration: underline; }}
.mi-news .src {{ color: var(--text-2); font-size: .78rem; margin-top: .15rem; }}

/* ---------- tabs ---------- */
.stTabs [data-baseweb="tab-list"] {{
  gap: 2px; border-bottom: 1px solid var(--border);
  background: transparent; margin-bottom: 14px;
}}
.stTabs [data-baseweb="tab"] {{
  height: 40px; padding: 0 15px; background: transparent;
  border: none; border-radius: 7px 7px 0 0;
  font-size: 0.855rem; font-weight: 560; color: var(--text-2);
  transition: color .14s ease, background .14s ease;
}}
.stTabs [data-baseweb="tab"]:hover {{ color: var(--text-1); background: var(--hover); }}
.stTabs [aria-selected="true"] {{ color: var(--accent) !important; font-weight: 660; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: var(--accent); height: 2px; }}
.stTabs [data-baseweb="tab-border"] {{ display: none; }}

/* ---------- section nav ----------
   Targeted via Streamlit's `st-key-<widget key>` container class, which is a
   stable hook tied to the widget's key. (A `.navrow + div` sibling selector does
   NOT work: each Streamlit element sits in its own wrapper, so a bare <div> in a
   markdown block is never the radio's sibling.)
   The nav reads as a tab bar and the date presets stay pills, so the two rows
   don't compete for primacy. */
.st-key-section {{ border-bottom: 1px solid var(--border); margin-bottom: 2px; }}
.st-key-section div[role="radiogroup"] {{ gap: 2px !important; }}
.st-key-section div[role="radiogroup"] > label {{
  background: transparent !important;
  border: none !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 7px 7px 0 0 !important;
  padding: 9px 16px 10px !important;
}}
.st-key-section div[role="radiogroup"] > label:hover {{
  background: var(--hover) !important; transform: none !important;
}}
.st-key-section div[role="radiogroup"] > label:has(input:checked) {{
  background: transparent !important;
  border-bottom: 2px solid var(--accent) !important;
}}
.st-key-section div[role="radiogroup"] > label:has(input:checked) p {{
  color: var(--accent) !important; font-weight: 680 !important;
}}
.st-key-section div[role="radiogroup"] > label p {{ font-size: 0.88rem !important; }}

/* ---------- sub-navigation, one level down ----------
   Keyed `sub_<page>` by `ui.sub_nav`, matched here on the key PREFIX so a new
   page needs no new rule. Deliberately lighter than the section nav above it:
   a filled segment on a recessed track rather than an underlined tab, so a
   reader can tell at a glance which row is the page and which is the view.
   Without the contrast the two rows compete and the hierarchy disappears. */
div[class*="st-key-sub_"] div[role="radiogroup"] {{
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 9px; padding: 3px; gap: 2px !important;
  display: inline-flex; margin: 10px 0 4px;
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label {{
  background: transparent !important; border: none !important;
  border-radius: 7px !important; padding: 5px 15px !important;
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label:hover {{
  background: var(--hover) !important; transform: none !important;
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label:has(input:checked) {{
  background: var(--surface) !important;
  border: 1px solid var(--border-strong) !important;
  padding: 4px 14px !important;  /* the border eats a pixel on each side */
  box-shadow: var(--shadow);
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label:has(input:checked) p {{
  color: var(--accent) !important; font-weight: 660 !important;
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label p {{ font-size: 0.815rem !important; }}

/* ---------- radios as Yahoo-style pills ---------- */
div[role="radiogroup"] {{ gap: 5px !important; flex-wrap: wrap; }}
div[role="radiogroup"] > label {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 7px; padding: 5px 13px; margin: 0 !important;
  cursor: pointer; transition: all .14s ease;
  font-size: 0.8rem; font-weight: 570;
}}
div[role="radiogroup"] > label:hover {{
  border-color: var(--accent); background: var(--hover); transform: translateY(-1px);
}}
@media (prefers-reduced-motion: reduce) {{ div[role="radiogroup"] > label:hover {{ transform: none; }} }}
div[role="radiogroup"] > label > div:first-child {{ display: none !important; }}  /* hide the dot */
div[role="radiogroup"] > label[data-checked="true"],
div[role="radiogroup"] > label:has(input:checked) {{
  background: var(--accent); border-color: var(--accent);
}}
div[role="radiogroup"] > label:has(input:checked) p,
div[role="radiogroup"] > label:has(input:checked) div {{
  color: #fff !important; font-weight: 660 !important;
}}
div[role="radiogroup"] > label p {{ font-size: 0.8rem !important; margin: 0 !important; }}

/* ---------- buttons ----------
   Selected by data-testid, NOT `.stButton > button`: passing `help=` to a button
   wraps it in .stTooltipHoverTarget instead of .stButton, so the descendant
   selector silently misses and the button renders white-on-white in dark mode.
   The testid holds regardless of wrapping. !important is needed to beat
   Streamlit's own emotion classes. */
button[data-testid^="stBaseButton"] {{
  background: var(--surface) !important;
  border: 1px solid var(--border-strong) !important;
  border-radius: 7px !important;
  color: var(--text-1) !important;
  padding: 5px 14px; font-size: 0.8rem; font-weight: 590;
  transition: all .14s ease; box-shadow: none !important;
}}
button[data-testid^="stBaseButton"] p,
button[data-testid^="stBaseButton"] div,
button[data-testid^="stBaseButton"] span {{ color: var(--text-1) !important; }}
button[data-testid^="stBaseButton"]:hover {{
  border-color: var(--accent) !important;
  background: var(--hover) !important; transform: translateY(-1px);
}}
button[data-testid^="stBaseButton"]:hover,
button[data-testid^="stBaseButton"]:hover p,
button[data-testid^="stBaseButton"]:hover div,
button[data-testid^="stBaseButton"]:hover span {{ color: var(--accent) !important; }}
button[data-testid^="stBaseButton"]:active {{ transform: translateY(0); }}
@media (prefers-reduced-motion: reduce) {{
  button[data-testid^="stBaseButton"]:hover {{ transform: none; }}
}}

/* ---------- inputs ---------- */
.stSelectbox div[data-baseweb="select"] > div,
.stMultiSelect div[data-baseweb="select"] > div,
.stTextInput input, .stNumberInput input {{
  background: var(--surface) !important;
  border-color: var(--border-strong) !important;
  border-radius: 7px !important;
  font-size: 0.85rem !important;
  color: var(--text-1) !important;
  transition: border-color .14s ease;
}}
/* Selected value / dropdown text. Without these the control keeps Streamlit's
   ink and the chosen option is unreadable on the dark surface. */
.stSelectbox div[data-baseweb="select"] div,
.stMultiSelect div[data-baseweb="select"] span,
.stNumberInput input, .stNumberInput div {{ color: var(--text-1) !important; }}
div[data-baseweb="popover"] li,
div[data-baseweb="popover"] div {{ color: var(--text-1) !important; }}
div[data-baseweb="popover"] ul {{
  background: var(--surface) !important;
  border: 1px solid var(--border-strong) !important;
}}
div[data-baseweb="popover"] li:hover {{ background: var(--hover) !important; }}
/* Number-input stepper buttons */
.stNumberInput button {{
  background: var(--surface-2) !important;
  border-color: var(--border) !important;
}}
.stNumberInput button svg {{ fill: var(--text-2) !important; }}
.stSelectbox div[data-baseweb="select"] > div:hover,
.stMultiSelect div[data-baseweb="select"] > div:hover,
.stTextInput input:hover {{ border-color: var(--accent) !important; }}
.stTextInput input::placeholder {{ color: var(--muted); }}
label[data-testid="stWidgetLabel"] p {{
  font-size: 0.71rem !important; font-weight: 640 !important;
  color: var(--muted) !important; text-transform: uppercase; letter-spacing: .06em;
}}
/* Multiselect tags. Streamlit's default tag is red, which in a finance UI reads
   as an error or a loss rather than "selected". Neutral surface + accent border. */
.stMultiSelect span[data-baseweb="tag"] {{
  background: var(--surface-2) !important;
  border: 1px solid var(--accent) !important;
  border-radius: 6px !important;
}}
.stMultiSelect span[data-baseweb="tag"] span,
.stMultiSelect span[data-baseweb="tag"] div {{
  color: var(--text-1) !important; font-weight: 600 !important;
}}
.stMultiSelect span[data-baseweb="tag"] svg {{ fill: var(--text-2) !important; }}
.stMultiSelect span[data-baseweb="tag"] [role="presentation"]:hover svg {{
  fill: var(--down) !important;
}}

/* ---------- code blocks ----------
   Streamlit keeps its own LIGHT syntax theme in both modes (the <pre> is
   #f8f9fb whatever the app theme). Its classed tokens carry explicit colors,
   but the *unclassed* ones -- plain identifiers like column names -- inherit
   from .stApp, so in dark mode they came out white on a near-white panel and
   vanished. Pin the panel and its plain ink together, and let the classed
   tokens keep their highlighting. */
[data-testid="stCode"] pre {{
  background: #f8f9fb !important;
  border: 1px solid rgba(11,11,11,0.10) !important;
  border-radius: 8px !important;
}}
[data-testid="stCode"] pre,
[data-testid="stCode"] code,
[data-testid="stCode"] code span:not([class]) {{ color: #31333f !important; }}
[data-testid="stCode"] code {{ font-family: {MONO_STACK}; font-size: 0.815rem; }}

/* ---------- dataframes ---------- */
[data-testid="stDataFrame"] {{
  border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden; box-shadow: var(--shadow);
}}
[data-testid="stExpander"] {{
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  background: var(--surface); box-shadow: var(--shadow);
}}
[data-testid="stExpander"] summary:hover {{ color: var(--accent); }}

/* ---------- plotly ---------- */
.js-plotly-plot .plotly .modebar {{
  background: transparent !important;
  opacity: 0; transition: opacity .18s ease;
}}
.js-plotly-plot:hover .plotly .modebar {{ opacity: 1; }}
.js-plotly-plot .plotly .modebar-btn path {{ fill: {p['muted']} !important; }}
.js-plotly-plot .plotly .modebar-btn:hover path {{ fill: {p['accent']} !important; }}
.stPlotlyChart {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 6px; box-shadow: var(--shadow);
}}

/* ---------- empty / notice states ----------
   Streamlit's st.warning and st.info draw their own yellow and blue panels,
   which ignore the validated palette and read as framework chrome dropped into
   a product. These carry the same information in the app's own surface, with
   the accent as a left rule so severity still has a non-color channel (the icon
   and the wording) rather than resting on the hue alone. */
.empty {{
  display: flex; gap: 11px; align-items: flex-start;
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid var(--muted);
  border-radius: var(--radius); padding: 13px 15px;
  margin: 8px 0 4px; box-shadow: var(--shadow);
}}
.empty-ico {{ font-size: 1rem; line-height: 1.35; flex: 0 0 auto; }}
.empty-body {{ min-width: 0; }}
.empty-msg {{ font-size: 0.83rem; font-weight: 560; color: var(--text-1); line-height: 1.45; }}
.empty-hint {{ font-size: 0.75rem; color: var(--muted); line-height: 1.5; margin-top: 3px; }}
.empty-warn {{ border-left-color: var(--down); }}
.empty-info {{ border-left-color: var(--accent); }}

/* ---------- skeleton (only for genuinely absent content) ---------- */
.skel {{
  background: linear-gradient(90deg, var(--surface-2) 25%, var(--border) 50%, var(--surface-2) 75%);
  background-size: 200% 100%; animation: shimmer 1.3s infinite;
  border-radius: 6px;
}}
@keyframes shimmer {{ 0% {{ background-position: 200% 0; }} 100% {{ background-position: -200% 0; }} }}
@media (prefers-reduced-motion: reduce) {{ .skel {{ animation: none; }} }}

/* ---------- Stock Journey ---------- */
/* The cursor headline. Large because during playback it is the only thing on
   the page that a reader tracks continuously. */
.jrn-head {{
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px 16px;
  padding: 14px 16px; margin: 6px 0 10px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; box-shadow: var(--shadow);
}}
.jrn-date {{
  font-size: 1.45rem; font-weight: 660; color: var(--text-1);
  letter-spacing: -0.01em; font-variant-numeric: tabular-nums;
}}
.jrn-price {{ font-size: 1.2rem; font-weight: 600; font-variant-numeric: tabular-nums; }}
.jrn-spacer {{ flex: 1 1 auto; }}
/* State chip. Carries an icon as well as a hue -- the house rule is that
   identity never rests on color alone. */
.jrn-chip {{
  font-size: 0.74rem; font-weight: 640; letter-spacing: .02em;
  padding: 4px 10px; border-radius: 999px; white-space: nowrap;
  border: 1px solid transparent;
}}
.jrn-chip.positive {{ color: var(--up); background: var(--up-bg); border-color: var(--up); }}
.jrn-chip.negative {{ color: var(--down); background: var(--down-bg); border-color: var(--down); }}
.jrn-chip.neutral  {{ color: var(--muted); background: var(--surface-2); border-color: var(--border); }}

/* Did You Know cards. */
.dyk-grid {{
  display: grid; gap: 10px; margin: 4px 0 8px;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}}
.dyk {{
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: 9px; padding: 11px 13px;
  transition: transform .16s ease, box-shadow .16s ease;
}}
.dyk:hover {{ transform: translateY(-2px); box-shadow: var(--shadow-hover); }}
.dyk.positive  {{ border-left-color: var(--up); }}
.dyk.negative  {{ border-left-color: var(--down); }}
.dyk.milestone {{ border-left-color: var(--accent); }}
.dyk-top {{ display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }}
.dyk-ico {{ font-size: 0.95rem; line-height: 1; }}
.dyk-head {{
  font-size: 0.86rem; font-weight: 640; color: var(--text-1);
  line-height: 1.3; font-variant-numeric: tabular-nums;
}}
.dyk-body {{ font-size: 0.755rem; color: var(--text-2); line-height: 1.5; }}

/* Timeline. A rail with one row per moment; scrolls inside its own box so a
   40-event company doesn't push the chart off the screen. */
.tl {{
  max-height: 420px; overflow-y: auto; padding: 2px 4px 2px 0;
  border-left: 2px solid var(--border); margin-left: 6px;
}}
.tl-row {{ position: relative; padding: 0 0 14px 20px; }}
.tl-row::before {{
  content: ""; position: absolute; left: -7px; top: 4px;
  width: 11px; height: 11px; border-radius: 50%;
  background: var(--muted); border: 2px solid var(--surface);
}}
.tl-row.positive::before  {{ background: var(--up); }}
.tl-row.negative::before  {{ background: var(--down); }}
.tl-row.milestone::before {{ background: var(--accent); }}
.tl-date {{
  font-size: 0.68rem; font-weight: 620; color: var(--muted);
  letter-spacing: .04em; text-transform: uppercase; font-variant-numeric: tabular-nums;
}}
.tl-title {{ font-size: 0.82rem; font-weight: 620; color: var(--text-1); line-height: 1.35; margin: 1px 0 2px; }}
.tl-body {{ font-size: 0.73rem; color: var(--text-2); line-height: 1.5; }}
.tl-move {{ font-size: 0.72rem; font-weight: 640; font-variant-numeric: tabular-nums; }}
.tl-move.up   {{ color: var(--up); }}
.tl-move.down {{ color: var(--down); }}
.tl-src {{ font-size: 0.66rem; color: var(--muted); opacity: .85; margin-top: 3px; }}
/* Kind badge: the third channel (with glyph and color) telling a curated
   company event apart from a market event and from a computed milestone. */
.tl-kind {{
  display: inline-block; font-size: 0.6rem; font-weight: 680; letter-spacing: .06em;
  text-transform: uppercase; padding: 1px 6px; border-radius: 4px;
  background: var(--surface-2); color: var(--muted); border: 1px solid var(--border);
  margin-left: 6px; vertical-align: 1px;
}}

@media (prefers-reduced-motion: reduce) {{
  .dyk {{ transition: none; }}
  .dyk:hover {{ transform: none; }}
}}

/* ---------- responsive ---------- */
@media (max-width: 900px) {{
  .hdr {{ flex-direction: column; align-items: flex-start; gap: 12px; }}
  .hdr-right {{ width: 100%; justify-content: space-between; }}
  .hdr-meta {{ text-align: left; }}
  .kpi-grid {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
  .kpi-val {{ font-size: 1.45rem; }}
  .q-price {{ font-size: 1.7rem; }}
  .block-container {{ padding-left: .9rem !important; padding-right: .9rem !important; }}
}}
@media (max-width: 560px) {{
  .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
  .quote {{ gap: 10px; }}
  .hdr-title {{ font-size: 1.12rem; }}
}}
</style>
"""
