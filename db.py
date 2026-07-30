"""SQLite connection helper.

Stock SQLite ships without the math functions (they need a compile flag Python's
bundled build doesn't set), so the ones the views and queries rely on are
registered here. Each guards its own domain and returns NULL rather than raising,
so a bad row yields a NULL cell instead of failing the whole query.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "sp500.db"


def _sqrt(x):
    if x is None or x < 0:
        return None
    return math.sqrt(x)


def _power(x, y):
    if x is None or y is None:
        return None
    try:
        return math.pow(x, y)
    except (ValueError, OverflowError):
        return None


def _ln(x):
    if x is None or x <= 0:
        return None
    return math.log(x)


def _exp(x):
    if x is None:
        return None
    try:
        return math.exp(x)
    except OverflowError:
        return None


class _Median:
    """MEDIAN(x) aggregate.

    SQLite has no median. It matters for cross-sectional return figures: over 25
    years an arithmetic mean of total returns is dominated by a single outlier
    (one +85,000% name drags a sector 'average' into the five figures), which
    describes that outlier rather than the sector.
    """

    def __init__(self) -> None:
        self.values: list[float] = []

    def step(self, value) -> None:
        if value is not None:
            self.values.append(value)

    def finalize(self):
        if not self.values:
            return None
        v = sorted(self.values)
        n = len(v)
        mid = n // 2
        return v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2.0


def _register(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.create_function("SQRT", 1, _sqrt)
    conn.create_function("POWER", 2, _power)
    conn.create_function("LN", 1, _ln)
    conn.create_function("EXP", 1, _exp)
    conn.create_aggregate("MEDIAN", 1, _Median)
    return conn


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    return _register(sqlite3.connect(db_path))


def get_readonly_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """A connection SQLite itself refuses to write through.

    Used for model-generated SQL. `sqlguard` already rejects anything that isn't a
    single SELECT, but that is string analysis and string analysis can be fooled;
    `mode=ro` is enforced by the engine, so a write that somehow survives the parser
    still fails at execution. Defense in depth, not a replacement for the guard.
    """
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    return _register(sqlite3.connect(uri, uri=True))
