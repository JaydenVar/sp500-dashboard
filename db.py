"""SQLite connection helper. Registers SQRT/POWER since stock SQLite ships without them."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "sp500.db"


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.create_function("SQRT", 1, lambda x: math.sqrt(x) if x is not None and x >= 0 else None)
    conn.create_function("POWER", 2, lambda x, y: math.pow(x, y) if x is not None and y is not None else None)
    return conn
