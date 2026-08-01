"""Palette values and the app's CSS — the MarketLens brand layer.

Colors are documented, validated values — categorical slots for series identity,
blue/red as the diverging pair, and a fixed status scale. Both modes are chosen
for their own surface rather than being an automatic inversion of each other.

Validated per mode (OKLCH lightness band, chroma floor, Machado-2009 protan/
deutan separation, normal-vision floor, WCAG contrast vs surface). The series
order is the colorblind-safety mechanism: assign slots in order, never cycle.

**The brand is one color, used everywhere, and it is NOT a series color.** The
categorical slots carry *data identity* and their separation is the thing that
was validated; a brand hue that also appeared in a chart legend would make "the
app's color" and "this company's color" the same signal. So `brand` is a
separate token, `accent` is aliased to it (every existing `var(--accent)` rule
adopts the brand with no call-site change), and `series` is untouched.

**The gains/losses scale is untouched too.** Green-up / red-down is a
convention a reader brings with them; rebranding it would be rebranding the
meaning of the numbers.
"""

from __future__ import annotations

BRAND_NAME = "MarketLens"
# The wordmark is drawn in two weights, so the split is data rather than a
# hardcoded slice in the component -- "Market" in primary ink, "Lens" in brand.
BRAND_SPLIT = ("Market", "Lens")
BRAND_TAGLINE = "Equity research, intelligence and risk — every figure computed in SQL"

# Categorical series slots, in fixed assignment order. UNCHANGED by the rebrand.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]

# ---------------------------------------------------------------------------
# THE BRAND RAMP — "Lens Violet -> Signal Blue"
#
# Picked per mode against that mode's own surface rather than being one hex used
# on both, which is the same rule the rest of this file already follows. Measured
# WCAG contrast vs. the surface the token is actually drawn on:
#
#   dark  #8B75F0 on #15151d -> 4.89:1   (the blue it replaces was 4.77:1)
#   light #5B3FD1 on #ffffff -> 6.83:1   (the blue it replaces was 4.42:1)
#
# Both are improvements, which matters because the accent is not decoration here
# -- it is the ink of selected tab labels, the Explorer's caption and every link.
# ---------------------------------------------------------------------------
BRAND_DARK = "#8B75F0"
BRAND_LIGHT = "#5B3FD1"

PALETTES = {
    "Light": dict(
        mode="light",
        surface="#ffffff",
        surface_2="#fafafd",
        page="#f5f6fa",
        text_primary="#0b0b12",
        text_secondary="#4a4a57",
        muted="#7b7b8a",
        gridline="#eaeaf1",
        baseline="#c2c2ce",
        border="rgba(11,11,18,0.09)",
        border_strong="rgba(11,11,18,0.16)",
        hover="rgba(91,63,209,0.06)",
        blue="#2a78d6",
        red="#e34948",
        neutral_mid="#f0f0f4",
        up="#0b7d3f",
        down="#c62828",
        up_bg="rgba(11,125,63,0.10)",
        down_bg="rgba(198,40,40,0.10)",
        brand=BRAND_LIGHT,
        brand_2="#2a78d6",
        brand_soft="rgba(91,63,209,0.10)",
        brand_glow="rgba(91,63,209,0.16)",
        accent=BRAND_LIGHT,
        series=SERIES_LIGHT,
        plotly_template="plotly_white",
        shadow="0 1px 2px rgba(11,11,18,0.04), 0 2px 8px rgba(11,11,18,0.05)",
        shadow_hover="0 2px 4px rgba(11,11,18,0.06), 0 10px 26px rgba(11,11,18,0.10)",
    ),
    "Dark": dict(
        mode="dark",
        # Cooled from the old warm near-blacks toward the brand's own hue. Every
        # surface here is DARKER than what it replaces, so each series color's
        # contrast against it goes up -- the validated set cannot regress.
        surface="#15151d",
        surface_2="#1c1c26",
        page="#0b0b12",
        text_primary="#ffffff",
        text_secondary="#c7c8d4",
        muted="#8b8b9c",
        gridline="#2a2a35",
        baseline="#3a3a48",
        border="rgba(255,255,255,0.09)",
        border_strong="rgba(255,255,255,0.17)",
        hover="rgba(139,117,240,0.13)",
        blue="#3987e5",
        red="#e66767",
        neutral_mid="#3a3a48",
        up="#3ec46b",
        down="#f2685f",
        up_bg="rgba(62,196,107,0.14)",
        down_bg="rgba(242,104,95,0.14)",
        brand=BRAND_DARK,
        brand_2="#3987e5",
        brand_soft="rgba(139,117,240,0.14)",
        brand_glow="rgba(139,117,240,0.22)",
        accent=BRAND_DARK,
        series=SERIES_DARK,
        plotly_template="plotly_dark",
        shadow="0 1px 2px rgba(0,0,0,0.45), 0 2px 10px rgba(0,0,0,0.35)",
        shadow_hover="0 2px 4px rgba(0,0,0,0.5), 0 12px 28px rgba(0,0,0,0.5)",
    ),
}

FONT_STACK = ('"Inter var", Inter, system-ui, -apple-system, "Segoe UI", Roboto, '
              '"Helvetica Neue", sans-serif')
MONO_STACK = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace'


def _rgba(hex_color: str, alpha: float) -> str:
    """A palette hex as a translucent fill.

    Local rather than imported from `charts.wash`: `charts` imports this module
    for its templates, and a stylesheet reaching back into the chart layer to
    tint a border would be a cycle for four characters of convenience.
    """
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


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
  --brand: {p['brand']};
  --brand-2: {p['brand_2']};
  --brand-soft: {p['brand_soft']};
  --brand-glow: {p['brand_glow']};
  /* `hover` was in the palette dict but never declared here, so every
     `background: var(--hover)` rule -- the nav, the sub-nav, the pills, the
     tabs -- resolved to nothing and was dropped. An undefined custom property
     fails SILENTLY: the element simply keeps what it inherited, which is why
     this survived. `tests_brand.py` now asserts every var() has a declaration. */
  --hover: {p['hover']};
  --brand-grad: linear-gradient(135deg, {p['brand']} 0%, {p['brand_2']} 100%);
  --up: {p['up']};
  --down: {p['down']};
  --up-bg: {p['up_bg']};
  --down-bg: {p['down_bg']};
  --shadow: {p['shadow']};
  --shadow-hover: {p['shadow_hover']};
  /* One radius scale and one motion curve, so nothing is rounded or eased by
     eye at a call site. */
  --radius-sm: 7px;
  --radius: 11px;
  --radius-lg: 16px;
  --ease: cubic-bezier(.22,.61,.36,1);
  --t-fast: .14s;
  --t: .2s;
}}

/* ---------- shell ---------- */
/* `color` here is load-bearing, not decoration: plain <div>s (the header title,
   card values) inherit it, and without it they keep Streamlit's own ink and go
   near-invisible in dark mode. */
.stApp {{ background: var(--page); color: var(--text-1); }}
/* A single, very wide brand wash anchored behind the top of the page. It is the
   cheapest way to stop a flat #0b0b12 reading as "a dark rectangle" -- depth
   comes from one light source, not from tinting every panel. */
.stApp::before {{
  content: ""; position: fixed; inset: 0 0 auto 0; height: 460px;
  pointer-events: none; z-index: 0;
  background:
    radial-gradient(900px 340px at 12% -8%, var(--brand-glow), transparent 70%),
    radial-gradient(760px 300px at 88% -12%, {_rgba(p['brand_2'], 0.14)}, transparent 72%);
}}
.stApp > * {{ position: relative; z-index: 1; }}
html, body, [class*="css"] {{ font-family: {FONT_STACK}; }}
body {{ -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }}
.block-container {{
  padding-top: 1.1rem !important;
  padding-bottom: 4rem !important;
  max-width: 1600px;
}}
#MainMenu, footer, header[data-testid="stHeader"] {{ visibility: hidden; height: 0; }}

h1, h2, h3, h4, h5 {{ color: var(--text-1); letter-spacing: -0.018em; }}
p, span, label, li {{ color: var(--text-1); }}
a {{ color: var(--brand); text-decoration-color: {_rgba(p['brand'], 0.4)}; }}
a:hover {{ text-decoration-color: var(--brand); }}
hr {{ border-color: var(--border); margin: 1.25rem 0; }}
/* Every figure in this app is a number in a column of numbers. Tabular figures
   are what stop them jittering between reruns. */
.kpi-val, .q-price, .mi-metric .v, .jrn-date, .jrn-price,
.tl-move, .spark-val {{ font-variant-numeric: tabular-nums; }}

/* Keyboard focus is a brand ring, not the browser default, and it is drawn on
   :focus-visible only so a mouse click does not leave a halo behind. */
*:focus-visible {{
  outline: 2px solid var(--brand) !important;
  outline-offset: 2px !important;
  border-radius: var(--radius-sm);
}}

/* Scrollbars, so a scrolling panel does not hand back the OS default in the
   middle of a dark product surface. */
* {{ scrollbar-width: thin; scrollbar-color: var(--border-strong) transparent; }}
*::-webkit-scrollbar {{ width: 9px; height: 9px; }}
*::-webkit-scrollbar-thumb {{
  background: var(--border-strong); border-radius: 999px;
  border: 2px solid transparent; background-clip: content-box;
}}
*::-webkit-scrollbar-thumb:hover {{ background: var(--brand); background-clip: content-box; }}
*::-webkit-scrollbar-track {{ background: transparent; }}

/* ---------- app bar ----------
   The product's masthead: brandmark, wordmark, then the session's own facts on
   the right. It is one bar rather than a title over a subtitle because a
   platform header is a fixed piece of furniture a reader stops reading after
   the first visit -- it should cost one row, not three. */
.hdr {{
  position: relative; overflow: hidden;
  display: flex; align-items: center; justify-content: space-between;
  gap: 20px; flex-wrap: wrap;
  background:
    linear-gradient(180deg, {_rgba(p['brand'], 0.05)}, transparent 62%),
    var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 20px;
  box-shadow: var(--shadow);
  margin-bottom: 12px;
}}
/* The brand rule along the top edge. One 2px gradient hairline does more for
   "this is a product" than any amount of panel tinting. */
.hdr::before {{
  content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
  background: var(--brand-grad);
}}
.hdr-left {{ display: flex; align-items: center; gap: 13px; min-width: 0; }}
.hdr-mark {{
  width: 38px; height: 38px; flex: 0 0 38px; display: block;
  border-radius: 11px;
  box-shadow: 0 4px 14px {_rgba(p['brand'], 0.34)};
  transition: transform var(--t) var(--ease), box-shadow var(--t) var(--ease);
}}
.hdr:hover .hdr-mark {{
  transform: translateY(-1px) scale(1.03);
  box-shadow: 0 6px 20px {_rgba(p['brand'], 0.45)};
}}
/* The wordmark. Two weights and two inks in one line -- the second half carries
   the brand gradient, which is the only place in the app text is painted. */
.hdr-title {{
  font-size: 1.34rem; line-height: 1.1; letter-spacing: -0.035em;
  font-weight: 620; color: var(--text-1); white-space: nowrap;
}}
.hdr-title b {{
  font-weight: 760;
  background: var(--brand-grad);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: var(--brand);
}}
.hdr-sub {{ font-size: 0.775rem; color: var(--muted); margin-top: 3px; letter-spacing: -0.002em; }}
.hdr-right {{ display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
.hdr-meta {{ font-size: 0.735rem; color: var(--muted); text-align: right; line-height: 1.5; }}
.hdr-meta b {{ color: var(--text-2); font-weight: 620; }}

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

/* ---------- metric cards ----------
   The number is the subject, so it gets the size and the tight tracking; the
   label above it is deliberately small, upper-case and muted so it reads as a
   caption rather than competing. Hover lifts the card and lights a brand rule
   along its top edge -- the same gesture as the app bar, one level down. */
.kpi-grid {{
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(196px, 1fr));
  margin: 6px 0 8px;
}}
.kpi {{
  position: relative; overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 15px 17px 16px;
  box-shadow: var(--shadow);
  transition: transform var(--t) var(--ease), box-shadow var(--t) var(--ease),
              border-color var(--t) var(--ease);
}}
.kpi::before {{
  content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
  background: var(--brand-grad);
  transform: scaleX(0); transform-origin: left;
  transition: transform var(--t) var(--ease);
}}
.kpi:hover {{
  transform: translateY(-3px); box-shadow: var(--shadow-hover);
  border-color: var(--border-strong);
}}
.kpi:hover::before {{ transform: scaleX(1); }}
.kpi-top {{ display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }}
.kpi-ico {{
  font-size: 0.8rem; line-height: 1; flex: 0 0 auto;
  width: 22px; height: 22px; border-radius: 6px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--brand-soft); color: var(--brand);
}}
.kpi-label {{
  font-size: 0.665rem; font-weight: 660; color: var(--muted);
  text-transform: uppercase; letter-spacing: .085em;
}}
.kpi-val {{
  font-size: 1.78rem; font-weight: 680; line-height: 1.08;
  color: var(--text-1); letter-spacing: -0.032em;
}}
.kpi-val.sm {{ font-size: 1.32rem; }}
.kpi-foot {{ font-size: 0.725rem; color: var(--muted); margin-top: 7px; line-height: 1.4; }}
.kpi-spark {{ margin-top: 10px; display: block; }}
.chg {{
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 0.745rem; font-weight: 660;
  padding: 3px 8px; border-radius: 999px; margin-top: 8px;
  border: 1px solid transparent;
}}
.chg-up {{ color: var(--up); background: var(--up-bg); border-color: {_rgba(p['up'], 0.25)}; }}
.chg-down {{ color: var(--down); background: var(--down-bg); border-color: {_rgba(p['down'], 0.25)}; }}
.chg-flat {{ color: var(--muted); background: var(--surface-2); border-color: var(--border); }}

/* ---------- sparklines ----------
   Inline SVG, drawn from a column this page already queried. Deliberately NOT
   Plotly: a sparkline is a shape, not an instrument -- it has no axes, no hover
   and no zoom, and four Plotly figures on the landing page would cost four
   WebGL-capable canvases to say something a 90-byte path says. */
.spark {{ display: block; width: 100%; height: auto; overflow: visible; }}
.spark-wrap {{ display: flex; align-items: center; gap: 9px; }}
.spark-val {{ font-size: 0.72rem; font-weight: 640; white-space: nowrap; }}

/* ---------- quote strip ---------- */
.quote {{
  position: relative; overflow: hidden;
  display: flex; align-items: flex-end; gap: 16px; flex-wrap: wrap;
  background:
    linear-gradient(180deg, {_rgba(p['brand'], 0.045)}, transparent 60%),
    var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius); padding: 17px 21px; box-shadow: var(--shadow);
  margin-bottom: 12px;
}}
.quote::before {{
  content: ""; position: absolute; inset: 0 auto 0 0; width: 3px;
  background: var(--brand-grad);
}}
.q-name {{ font-size: 1.12rem; font-weight: 680; letter-spacing: -0.022em; }}
.q-tkr {{
  font-family: {MONO_STACK}; font-size: 0.71rem; font-weight: 680;
  color: var(--brand); background: var(--brand-soft);
  border: 1px solid {_rgba(p['brand'], 0.28)}; border-radius: 6px;
  padding: 2px 7px; margin-left: 9px; vertical-align: 2px;
  letter-spacing: .02em;
}}
.q-meta {{ font-size: 0.745rem; color: var(--muted); margin-top: 4px; }}
.q-price {{ font-size: 2.15rem; font-weight: 700; line-height: 1; letter-spacing: -0.038em; }}
.q-chg {{ font-size: 0.95rem; font-weight: 670; margin-left: 2px; }}

/* ---------- ask the market ---------- */
.answer {{
  position: relative; overflow: hidden;
  background:
    linear-gradient(120deg, var(--brand-soft), transparent 55%),
    var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 19px 16px 22px;
  margin: 4px 0 12px;
  font-size: 1.04rem; line-height: 1.55; color: var(--text-1);
  box-shadow: var(--shadow);
}}
.answer::before {{
  content: ""; position: absolute; inset: 0 auto 0 0; width: 3px;
  background: var(--brand-grad);
}}
.st-key-ask_q input {{ font-size: 1rem !important; padding: 10px 14px !important; }}

/* ---------- developer center ---------- */
.qbox {{
  position: relative; overflow: hidden;
  background: linear-gradient(135deg, var(--brand-soft), transparent 70%);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 18px 14px 21px;
  margin: 6px 0 14px;
}}
.qbox::before {{
  content: ""; position: absolute; inset: 0 auto 0 0; width: 3px;
  background: var(--brand-grad);
}}
.qbox-k {{
  font-size: 0.655rem; font-weight: 700; color: var(--brand);
  text-transform: uppercase; letter-spacing: .1em; margin-bottom: 5px;
}}
.qbox-q {{ font-size: 1.08rem; font-weight: 640; color: var(--text-1); line-height: 1.4; }}
.uses {{
  display: inline-block;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 6px 12px; margin: 0 7px 7px 0;
  font-size: 0.82rem; color: var(--text-1);
  transition: border-color var(--t-fast) var(--ease), background var(--t-fast) var(--ease);
}}
.uses:hover {{ border-color: var(--brand); background: var(--brand-soft); }}
.modepill {{
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 0.665rem; font-weight: 720; letter-spacing: .085em;
  text-transform: uppercase; padding: 4px 11px; border-radius: 999px;
  background: var(--surface-2); border: 1px solid var(--border-strong);
  color: var(--text-2);
}}
.modepill.dev {{
  border-color: var(--brand); color: var(--brand); background: var(--brand-soft);
}}

/* ---------- section headings ----------
   A short brand tick before the title. It costs 3px and it is what turns a
   stack of bold lines into a scannable hierarchy -- the eye finds the ticks. */
.sec {{ display: flex; align-items: baseline; gap: 10px; margin: 26px 0 6px; }}
.sec-t {{
  position: relative; padding-left: 11px;
  font-size: 1.02rem; font-weight: 680; letter-spacing: -0.02em; color: var(--text-1);
}}
.sec-t::before {{
  content: ""; position: absolute; left: 0; top: .18em; bottom: .18em; width: 3px;
  border-radius: 999px; background: var(--brand-grad);
}}
.sec-s {{ font-size: 0.765rem; color: var(--muted); }}
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 6px 10px 2px;
  box-shadow: var(--shadow); margin-bottom: 6px;
}}
.note {{ font-size: 0.75rem; color: var(--muted); line-height: 1.55; }}

/* ---------- detail cards ----------
   One surface, one border, one spacing scale for every label/value panel in the
   app. These began life scoped to the Intelligence page, which is exactly why
   that page read tighter than the rest -- so they are global now and reached
   through `ui.card()` / `ui.metric_rows()`. Every color is a palette variable
   that already passed the contrast and color-vision validation. */
.mi-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: .9rem 1.05rem; margin-bottom: .65rem;
  box-shadow: var(--shadow);
  transition: border-color var(--t) var(--ease), box-shadow var(--t) var(--ease),
              transform var(--t) var(--ease);
}}
.mi-card:hover {{
  border-color: var(--border-strong); box-shadow: var(--shadow-hover);
  transform: translateY(-2px);
}}
.mi-card h4 {{
  margin: 0 0 .5rem 0; font-size: .705rem; font-weight: 680; color: var(--muted);
  text-transform: uppercase; letter-spacing: .085em;
}}
.mi-metric {{
  display: flex; justify-content: space-between; gap: 1rem;
  padding: .36rem 0; border-bottom: 1px solid var(--border); font-size: .885rem;
}}
.mi-metric:last-child {{ border-bottom: none; }}
.mi-metric .k {{ color: var(--text-2); }}
.mi-metric .v {{ color: var(--text-1); font-weight: 640; font-variant-numeric: tabular-nums; }}
.mi-pct {{ font-size: .78rem; color: var(--text-2); margin-left: .4rem; }}
.mi-bar {{
  height: 6px; border-radius: 999px; background: var(--surface-2);
  overflow: hidden; margin-top: .3rem; border: 1px solid var(--border);
}}
.mi-bar > span {{
  display: block; height: 100%; border-radius: 999px;
  transition: width .5s var(--ease);
}}
.mi-chip {{
  display: inline-block; min-width: 2.4rem; text-align: center;
  padding: .16rem .5rem; border-radius: 999px; font-weight: 720; font-size: .86rem;
  font-variant-numeric: tabular-nums;
}}
.mi-news {{
  padding: .5rem .55rem; border-bottom: 1px solid var(--border);
  border-radius: var(--radius-sm); margin: 0 -.55rem;
  transition: background var(--t-fast) var(--ease);
}}
.mi-news:hover {{ background: var(--brand-soft); }}
.mi-news:last-child {{ border-bottom: none; }}
.mi-news a {{ color: var(--text-1); text-decoration: none; font-weight: 620; font-size: .9rem; }}
.mi-news a:hover {{ color: var(--brand); }}
.mi-news .src {{ color: var(--muted); font-size: .765rem; margin-top: .2rem; }}

/* ---------- the Research hero ----------
   The landing page's entry point, as one panel rather than two loose inputs on
   the page background. The greeting is the only sentence on the page addressed
   to the reader, and it deliberately carries NO name -- the app knows a session,
   not a person, and a greeting that guesses at one is worse than none.

   Hooked via `st.container(key=...)`, which is the only reliable way to wrap
   real Streamlit widgets in a styled surface: raw HTML cannot contain them, and
   each widget sits in its own wrapper so sibling selectors never reach. */
.st-key-rs_hero {{
  position: relative; overflow: hidden;
  background:
    radial-gradient(680px 220px at 8% 0%, {_rgba(p['brand'], 0.10)}, transparent 68%),
    radial-gradient(560px 200px at 92% 8%, {_rgba(p['brand_2'], 0.09)}, transparent 70%),
    var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px 24px 18px;
  margin-bottom: 14px;
  box-shadow: var(--shadow);
}}
.st-key-rs_hero::before {{
  content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
  background: var(--brand-grad); opacity: .85;
}}
.hero-greet {{
  font-size: 1.62rem; font-weight: 700; letter-spacing: -0.035em;
  color: var(--text-1); line-height: 1.15;
}}
.hero-greet em {{
  font-style: normal;
  background: var(--brand-grad);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: var(--brand);
}}
.hero-sub {{
  font-size: 0.85rem; color: var(--text-2); margin: 5px 0 15px; line-height: 1.5;
}}
/* The hero's own inputs are the largest controls on the page, because they are
   the page's purpose. Scoped to the hero so the app's other inputs keep the
   compact scale the dense pages need. */
.st-key-rs_hero .stTextInput input {{
  font-size: 0.95rem !important; padding: 11px 15px !important;
  border-radius: 9px !important; background: var(--surface-2) !important;
}}
.st-key-rs_hero .stTextInput input:focus {{
  border-color: var(--brand) !important;
  box-shadow: 0 0 0 3px var(--brand-soft) !important;
}}

/* ---------- the Research entry row ----------
   A label above each box, because two bare inputs side by side do not say which
   one takes a ticker and which takes a sentence. */
.rs-lbl {{
  display: flex; align-items: center; gap: 6px;
  font-size: 0.675rem; font-weight: 680; color: var(--muted);
  text-transform: uppercase; letter-spacing: .085em; margin: 2px 0 6px;
}}
.rs-lbl::before {{
  content: ""; width: 5px; height: 5px; border-radius: 50%;
  background: var(--brand-grad); flex: 0 0 5px;
}}

/* ---------- Today's Opportunities, one row ----------
   This was four cards with a button under each -- ~250px of the first screen,
   which pushed the searched company's own panel below the fold. The engine still
   has to be visible without scrolling, so the strip shrank to a single row
   instead of moving. The score band's color rides on each button's left edge
   (the per-symbol rules are generated in research.py, since the key carries the
   ticker); the score itself is in the label, so color is never the only carrier. */
.pick-lead {{
  display: flex; flex-direction: column; justify-content: center;
  height: 100%; min-height: 66px; line-height: 1.3;
  font-size: 0.675rem; font-weight: 700; color: var(--text-2);
  letter-spacing: .085em; text-transform: uppercase;
}}
.pick-lead span {{
  font-size: 0.7rem; font-weight: 500; color: var(--muted);
  letter-spacing: 0; text-transform: none; margin-top: 3px;
}}

/* The pick chips became CARDS without becoming a card GRID.
   The original four-card block cost ~250px of the first screen and pushed the
   searched company's own panel below the fold, which is why it was collapsed to
   a row of chips. It is still one row -- the card is the button itself: two
   lines of label (ticker + score, then the company name) and a sparkline
   painted as a background image on the right, generated per symbol in
   `research.py` alongside the existing per-symbol border rule. Total cost over
   the chip row is ~28px, not ~210px, so the engine stays above the fold and the
   reader still gets the shape of the move. */
div[class*="st-key-rs_pick_"] button {{
  min-height: 66px !important;
  padding: 9px 13px !important;
  align-items: flex-start !important;
  text-align: left !important;
  background: var(--surface) !important;
  background-repeat: no-repeat !important;
  background-position: right 10px bottom 9px !important;
  border-radius: var(--radius) !important;
  overflow: hidden;
}}
div[class*="st-key-rs_pick_"] button > div {{ width: 100%; text-align: left; }}
div[class*="st-key-rs_pick_"] button p {{
  text-align: left !important; line-height: 1.3 !important;
}}
div[class*="st-key-rs_pick_"] button:hover {{
  background: var(--brand-soft) !important;
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover) !important;
}}
/* The disabled chip is a STATE, not a broken control, so it keeps the brand
   rather than going grey -- a greyed-out button in a row of live ones reads as
   "this one is unavailable", the opposite of what is true. */
div[class*="st-key-rs_pick_"] button:disabled,
div[class*="st-key-rs_pick_"] button:disabled:hover {{
  background-color: {_rgba(p['brand'], 0.15)} !important;
  border-color: var(--brand) !important;
  color: var(--brand) !important;
  opacity: 1 !important;
  cursor: default !important;
  transform: none !important;
  box-shadow: inset 0 0 0 1px {_rgba(p['brand'], 0.35)} !important;
}}
div[class*="st-key-rs_pick_"] button:disabled p {{
  color: var(--brand) !important; font-weight: 680 !important;
}}
/* The jump-to-engine control is the row's one outbound action, so it is the
   only filled button on the landing page. */
.st-key-rs_to_engine button {{
  min-height: 66px !important;
  background: var(--brand-grad) !important;
  border-color: transparent !important;
  box-shadow: 0 4px 16px {_rgba(p['brand'], 0.3)} !important;
}}
.st-key-rs_to_engine button p,
.st-key-rs_to_engine button:hover p {{ color: #fff !important; font-weight: 660 !important; }}
.st-key-rs_to_engine button:hover {{
  filter: brightness(1.08);
  box-shadow: 0 6px 22px {_rgba(p['brand'], 0.42)} !important;
}}

/* ---------- tabs ---------- */
.stTabs [data-baseweb="tab-list"] {{
  gap: 2px; border-bottom: 1px solid var(--border);
  background: transparent; margin-bottom: 14px;
}}
.stTabs [data-baseweb="tab"] {{
  height: 40px; padding: 0 15px; background: transparent;
  border: none; border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  font-size: 0.855rem; font-weight: 570; color: var(--text-2);
  transition: color var(--t-fast) var(--ease), background var(--t-fast) var(--ease);
}}
.stTabs [data-baseweb="tab"]:hover {{ color: var(--text-1); background: var(--hover); }}
.stTabs [aria-selected="true"] {{ color: var(--brand) !important; font-weight: 680; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: var(--brand-grad); height: 2px; }}
.stTabs [data-baseweb="tab-border"] {{ display: none; }}

/* ---------- section nav ----------
   Targeted via Streamlit's `st-key-<widget key>` container class, which is a
   stable hook tied to the widget's key. (A `.navrow + div` sibling selector does
   NOT work: each Streamlit element sits in its own wrapper, so a bare <div> in a
   markdown block is never the radio's sibling.)
   The nav reads as a tab bar and the date presets stay pills, so the two rows
   don't compete for primacy.

   The selected underline is a scaled pseudo-element rather than a border, so it
   GROWS from the centre when a page is chosen instead of appearing instantly.
   A border-bottom cannot be transitioned into existence; a transform can, and
   it is the one animation on this row that carries information -- it says which
   item you just moved to. */
.st-key-section {{ border-bottom: 1px solid var(--border); margin-bottom: 2px; }}
.st-key-section div[role="radiogroup"] {{ gap: 2px !important; }}
.st-key-section div[role="radiogroup"] > label {{
  position: relative;
  background: transparent !important;
  border: none !important;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0 !important;
  padding: 10px 17px 11px !important;
  transition: background var(--t-fast) var(--ease);
}}
.st-key-section div[role="radiogroup"] > label::after {{
  content: ""; position: absolute; left: 10px; right: 10px; bottom: -1px; height: 2px;
  border-radius: 2px 2px 0 0; background: var(--brand-grad);
  transform: scaleX(0); transition: transform var(--t) var(--ease);
}}
.st-key-section div[role="radiogroup"] > label:hover {{
  background: var(--hover) !important; transform: none !important;
}}
.st-key-section div[role="radiogroup"] > label:hover::after {{ transform: scaleX(.55); opacity: .5; }}
.st-key-section div[role="radiogroup"] > label:has(input:checked) {{
  /* The generic pill rule fills and shadows a checked radio; the nav is a tab
     bar, so both are undone here rather than by excluding the nav upstream. */
  background: transparent !important; box-shadow: none !important;
}}
.st-key-section div[role="radiogroup"] > label:has(input:checked)::after {{
  transform: scaleX(1); opacity: 1;
}}
.st-key-section div[role="radiogroup"] > label:has(input:checked) p {{
  color: var(--brand) !important; font-weight: 700 !important;
}}
.st-key-section div[role="radiogroup"] > label p {{
  font-size: 0.885rem !important; letter-spacing: -0.006em;
}}

/* ---------- sub-navigation, one level down ----------
   Keyed `sub_<page>` by `ui.sub_nav`, matched here on the key PREFIX so a new
   page needs no new rule. Deliberately lighter than the section nav above it:
   a filled segment on a recessed track rather than an underlined tab, so a
   reader can tell at a glance which row is the page and which is the view.
   Without the contrast the two rows compete and the hierarchy disappears. */
div[class*="st-key-sub_"] div[role="radiogroup"] {{
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 10px; padding: 3px; gap: 2px !important;
  display: inline-flex; margin: 12px 0 6px;
  box-shadow: inset 0 1px 2px {_rgba('#000000' if p['mode'] == 'dark' else '#0b0b12', 0.14)};
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label {{
  background: transparent !important; border: none !important;
  border-radius: var(--radius-sm) !important; padding: 5px 15px !important;
  transition: background var(--t-fast) var(--ease), color var(--t-fast) var(--ease);
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label:hover {{
  background: var(--hover) !important; transform: none !important;
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label:has(input:checked) {{
  background: var(--surface) !important;
  border: 1px solid {_rgba(p['brand'], 0.35)} !important;
  padding: 4px 14px !important;  /* the border eats a pixel on each side */
  box-shadow: var(--shadow);
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label:has(input:checked) p {{
  color: var(--brand) !important; font-weight: 680 !important;
}}
div[class*="st-key-sub_"] div[role="radiogroup"] > label p {{ font-size: 0.815rem !important; }}

/* ---------- radios as pills ---------- */
div[role="radiogroup"] {{ gap: 5px !important; flex-wrap: wrap; }}
div[role="radiogroup"] > label {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 5px 13px; margin: 0 !important;
  cursor: pointer;
  transition: background var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease),
              transform var(--t-fast) var(--ease), box-shadow var(--t-fast) var(--ease);
  font-size: 0.8rem; font-weight: 580;
}}
div[role="radiogroup"] > label:hover {{
  border-color: var(--brand); background: var(--hover); transform: translateY(-1px);
}}
@media (prefers-reduced-motion: reduce) {{ div[role="radiogroup"] > label:hover {{ transform: none; }} }}
div[role="radiogroup"] > label > div:first-child {{ display: none !important; }}  /* hide the dot */
div[role="radiogroup"] > label[data-checked="true"],
div[role="radiogroup"] > label:has(input:checked) {{
  background: var(--brand-grad); border-color: transparent;
  box-shadow: 0 2px 10px {_rgba(p['brand'], 0.3)};
}}
div[role="radiogroup"] > label:has(input:checked) p,
div[role="radiogroup"] > label:has(input:checked) div {{
  color: #fff !important; font-weight: 670 !important;
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
  border-radius: var(--radius-sm) !important;
  color: var(--text-1) !important;
  padding: 6px 15px; font-size: 0.8rem; font-weight: 600;
  transition: background var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease),
              color var(--t-fast) var(--ease), transform var(--t-fast) var(--ease),
              box-shadow var(--t-fast) var(--ease);
  box-shadow: none !important;
}}
button[data-testid^="stBaseButton"] p,
button[data-testid^="stBaseButton"] div,
button[data-testid^="stBaseButton"] span {{ color: var(--text-1) !important; }}
button[data-testid^="stBaseButton"]:hover {{
  border-color: var(--brand) !important;
  background: var(--brand-soft) !important; transform: translateY(-1px);
  box-shadow: 0 3px 12px {_rgba(p['brand'], 0.18)} !important;
}}
button[data-testid^="stBaseButton"]:hover,
button[data-testid^="stBaseButton"]:hover p,
button[data-testid^="stBaseButton"]:hover div,
button[data-testid^="stBaseButton"]:hover span {{ color: var(--brand) !important; }}
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
.stTextInput input:hover {{ border-color: var(--brand) !important; }}
/* A focus ring on the control itself, so the field a reader is typing into is
   obvious on a dark surface where a 1px border change is not. */
.stTextInput input:focus,
.stNumberInput input:focus {{
  border-color: var(--brand) !important;
  box-shadow: 0 0 0 3px var(--brand-soft) !important;
}}
.stTextInput input::placeholder {{ color: var(--muted); }}
label[data-testid="stWidgetLabel"] p {{
  font-size: 0.7rem !important; font-weight: 670 !important;
  color: var(--muted) !important; text-transform: uppercase; letter-spacing: .075em;
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
  transition: border-color var(--t-fast) var(--ease);
}}
[data-testid="stExpander"]:hover {{ border-color: var(--border-strong) !important; }}
[data-testid="stExpander"] summary {{ transition: color var(--t-fast) var(--ease); }}
[data-testid="stExpander"] summary:hover {{ color: var(--brand); }}

/* ---------- plotly ---------- */
.js-plotly-plot .plotly .modebar {{
  background: transparent !important;
  opacity: 0; transition: opacity .18s ease;
}}
.js-plotly-plot:hover .plotly .modebar {{ opacity: 1; }}
.js-plotly-plot .plotly .modebar-btn path {{ fill: {p['muted']} !important; }}
.js-plotly-plot .plotly .modebar-btn:hover path {{ fill: {p['brand']} !important; }}
.stPlotlyChart {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 7px; box-shadow: var(--shadow);
  transition: border-color var(--t) var(--ease), box-shadow var(--t) var(--ease);
}}
.stPlotlyChart:hover {{ border-color: var(--border-strong); box-shadow: var(--shadow-hover); }}

/* ---------- empty / notice states ----------
   Streamlit's st.warning and st.info draw their own yellow and blue panels,
   which ignore the validated palette and read as framework chrome dropped into
   a product. These carry the same information in the app's own surface, with
   the accent as a left rule so severity still has a non-color channel (the icon
   and the wording) rather than resting on the hue alone. */
.empty {{
  display: flex; gap: 12px; align-items: flex-start;
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid var(--muted);
  border-radius: var(--radius); padding: 14px 16px;
  margin: 8px 0 4px; box-shadow: var(--shadow);
}}
.empty-ico {{
  font-size: 0.9rem; line-height: 1; flex: 0 0 auto;
  width: 24px; height: 24px; border-radius: 7px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--surface-2); border: 1px solid var(--border); color: var(--text-2);
}}
.empty-body {{ min-width: 0; }}
.empty-msg {{ font-size: 0.835rem; font-weight: 580; color: var(--text-1); line-height: 1.45; }}
.empty-hint {{ font-size: 0.75rem; color: var(--muted); line-height: 1.55; margin-top: 4px; }}
.empty-warn {{ border-left-color: var(--down); }}
.empty-warn .empty-ico {{ background: var(--down-bg); border-color: {_rgba(p['down'], 0.3)}; color: var(--down); }}
.empty-info {{ border-left-color: var(--brand); }}
.empty-info .empty-ico {{ background: var(--brand-soft); border-color: {_rgba(p['brand'], 0.3)}; color: var(--brand); }}

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
  position: relative; overflow: hidden;
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px 16px;
  padding: 15px 17px; margin: 6px 0 10px;
  background:
    linear-gradient(120deg, var(--brand-soft), transparent 48%),
    var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);
}}
.jrn-head::before {{
  content: ""; position: absolute; inset: 0 auto 0 0; width: 3px;
  background: var(--brand-grad);
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
  border-radius: var(--radius); padding: 12px 14px;
  box-shadow: var(--shadow);
  transition: transform var(--t) var(--ease), box-shadow var(--t) var(--ease),
              border-color var(--t) var(--ease);
}}
.dyk:hover {{
  transform: translateY(-3px); box-shadow: var(--shadow-hover);
  border-color: var(--border-strong);
}}
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
  .hero-greet {{ font-size: 1.34rem; }}
  .st-key-rs_hero {{ padding: 16px 16px 14px; }}
  .block-container {{ padding-left: .9rem !important; padding-right: .9rem !important; }}
}}
@media (max-width: 560px) {{
  .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
  .quote {{ gap: 10px; }}
  .hdr-title {{ font-size: 1.12rem; }}
  .hero-greet {{ font-size: 1.2rem; }}
}}

/* One blanket stop for the motion this file introduces. Every individual rule
   that moves something also has its own reduced-motion guard; this catches the
   transitions declared on shared tokens. */
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation-duration: .001ms !important; animation-iteration-count: 1 !important;
    transition-duration: .001ms !important;
  }}
  .kpi:hover, .mi-card:hover, .dyk:hover,
  div[class*="st-key-rs_pick_"] button:hover {{ transform: none; }}
}}
</style>
"""
