"""Validator for model-generated SQL.

Nothing the model writes reaches the database without passing every check here.
The order matters — cheap structural rejections run before anything touches
SQLite, so a hostile string is refused without ever being parsed by the engine.

What this module refuses to do:

* **Trust a prefix.** `"SELECT 1; DROP TABLE prices"` starts with SELECT. Statement
  count is checked separately, and a payload with a second statement is rejected
  whatever the first one looks like.
* **Read keywords through comments.** `SELECT/**/DROP` and trailing `--` are the
  standard way to smuggle a token past a naive scan, so comments are stripped
  before analysis and the *stripped* text is what executes.
* **Be the only line of defense.** This is string analysis, and string analysis can
  be fooled. Execution happens on `db.get_readonly_connection()`, where SQLite
  itself rejects writes. Both layers, always.

Errors are returned, never raised: the caller shows the user a plain sentence and
keeps the app alive. `reason` is for the Developer Center; `message` is for people.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from build_db import REQUIRED_OBJECTS
from db import get_readonly_connection

# The only objects model-generated SQL may read. Sourced from build_db so a schema
# change can never silently widen what the model is allowed to touch. `meta` is
# deliberately absent -- it holds the schema stamp, not market data.
ALLOWED_OBJECTS = frozenset(REQUIRED_OBJECTS)

# Anything that writes, changes schema, attaches a file, or reaches outside the
# query. Matched on word boundaries so `created_at` is not mistaken for CREATE.
_FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "pragma", "attach", "detach", "vacuum", "reindex",
    "grant", "revoke", "commit", "rollback", "savepoint", "begin",
)
_FORBIDDEN_RE = re.compile(rf"\b({'|'.join(_FORBIDDEN)})\b", re.IGNORECASE)

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CTE_NAME = re.compile(r"(?:\bwith\b|,)\s*(?:recursive\s+)?([a-z_][a-z0-9_]*)\s+as\s*\(", re.IGNORECASE)
_SOURCE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    """Outcome of validation. `sql` is the cleaned text to execute, if allowed."""
    ok: bool
    sql: str = ""
    reason: str = ""      # technical, shown in the Developer Center
    message: str = ""     # user-facing, shown in User Mode


def _strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))


def _deny(reason: str, message: str) -> Verdict:
    return Verdict(ok=False, reason=reason, message=message)


def validate(sql: str) -> Verdict:
    """Check model-generated SQL against every rule. Never raises."""
    if not sql or not sql.strip():
        return _deny("empty statement", "I couldn't turn that into a question about the data.")

    cleaned = _strip_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        return _deny("comment-only statement",
                     "I couldn't turn that into a question about the data.")

    # One statement only. Checked before the SELECT test, because a compound
    # payload whose first statement is a SELECT would otherwise pass.
    if ";" in cleaned:
        return _deny("multiple statements",
                     "That needed more than one query to answer, which I don't run.")

    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        return _deny("does not begin with SELECT or WITH",
                     "I can only read data, and that asked for something else.")

    hit = _FORBIDDEN_RE.search(cleaned)
    if hit:
        return _deny(f"forbidden keyword: {hit.group(1).upper()}",
                     "That would have changed the data, so I stopped. This app only reads.")

    # Table allowlist. CTE names are defined by the query itself, so they are
    # collected first and exempted -- otherwise every WITH clause looks like an
    # attempt to read an unapproved table.
    ctes = {m.group(1).lower() for m in _CTE_NAME.finditer(cleaned)}
    for m in _SOURCE.finditer(cleaned):
        name = m.group(1).lower()
        if name in ctes or name in ALLOWED_OBJECTS:
            continue
        return _deny(f"unapproved object: {name}",
                     f"That asked for “{name}”, which isn't part of this dataset.")

    # Syntax and column names, checked against the real schema. EXPLAIN compiles
    # the statement without running it, so a hallucinated column is caught here
    # rather than surfacing as an exception mid-render.
    conn = get_readonly_connection()
    try:
        conn.execute(f"EXPLAIN {cleaned}")
    except sqlite3.Error as exc:
        return _deny(f"sqlite rejected it: {exc}",
                     "I couldn't build a valid query for that. Try rephrasing it.")
    finally:
        conn.close()

    return Verdict(ok=True, sql=cleaned)
