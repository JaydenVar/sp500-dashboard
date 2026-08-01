"""The per-run context every page module receives.

`app.py` resolves the theme, the directory and the shared date window exactly
once, then hands the result to whichever page is active. Bundling those into one
frozen object rather than passing eight positional arguments is what keeps the
page signatures stable: adding a field here does not touch a single call site,
which matters because the same window has to reach five pages and a dozen
sub-views.

Frozen on purpose. A page that could reassign `ctx.end` would be able to
re-baseline a figure another page also shows, which is precisely the shared-window
guarantee the app is built on -- one value, decided once, above everything it
scopes.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Ctx:
    """Everything a page needs that `app.py` already computed.

    `start`/`end` are the ISO strings the queries take; `start_d`/`end_d` are the
    same instants as dates, for formatting and for clamping against a symbol's
    own listed history. Both are carried rather than converted per page because
    the conversion happened in three places and drifted once already.
    """

    pal: dict
    directory: pd.DataFrame
    equities: pd.DataFrame
    preset: str
    start_d: dt.date
    end_d: dt.date
    start: str
    end: str
    last_date: dt.date
    index_min: dt.date
    index_max: dt.date
