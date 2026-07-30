"""Adversarial checks for the generated-SQL validator.

sqlguard is a security boundary: it is the only thing between a model's output
and the database. Every case below is an attack shape that a naive check lets
through -- a stacked statement behind a valid SELECT, a keyword hidden in a
comment, a read of sqlite_master. The read-only connection is asserted too,
because the guard is explicitly not trusted to be sufficient on its own.

Run: ./.venv/bin/python tests_sqlguard.py
"""

import sys

import sqlguard

ALLOW = [
    ("plain select", "SELECT symbol FROM symbols LIMIT 5"),
    ("trailing semicolon", "SELECT symbol FROM symbols LIMIT 5;"),
    ("with-cte", "WITH w AS (SELECT symbol, close FROM prices LIMIT 10) SELECT * FROM w"),
    ("multi-cte", "WITH a AS (SELECT 1 AS x), b AS (SELECT 2 AS y) SELECT * FROM a, b"),
    ("join + alias", "SELECT s.symbol FROM symbols s JOIN prices p ON p.symbol = s.symbol LIMIT 1"),
    ("rollup table", "SELECT symbol, cagr FROM symbol_stats ORDER BY cagr DESC LIMIT 5"),
    ("view", "SELECT symbol, drawdown FROM drawdowns LIMIT 3"),
    ("udf math", "SELECT SQRT(4.0) AS r"),
    ("word containing keyword", "SELECT first_date AS created_at FROM symbol_stats LIMIT 1"),
]

DENY = [
    ("empty", ""),
    ("whitespace", "   \n  "),
    ("comment only", "-- just a comment"),
    ("stacked drop", "SELECT 1; DROP TABLE prices"),
    ("stacked delete", "SELECT symbol FROM symbols; DELETE FROM prices"),
    ("bare drop", "DROP TABLE prices"),
    ("update", "UPDATE prices SET close = 0"),
    ("insert", "INSERT INTO symbols VALUES ('X','x','y','z',0)"),
    ("delete", "DELETE FROM prices WHERE 1=1"),
    ("alter", "ALTER TABLE prices ADD COLUMN hacked TEXT"),
    ("create", "CREATE TABLE evil (x INT)"),
    ("pragma", "PRAGMA table_info(prices)"),
    ("attach", "ATTACH DATABASE '/tmp/evil.db' AS evil"),
    ("vacuum", "VACUUM"),
    ("block-comment smuggle", "SELECT 1/**/; DROP TABLE prices"),
    ("comment-hidden drop", "SELECT symbol FROM symbols -- \nDROP TABLE prices"),
    ("sqlite_master", "SELECT name FROM sqlite_master"),
    ("meta table", "SELECT value FROM meta"),
    ("unknown table", "SELECT * FROM users"),
    ("hallucinated column", "SELECT nonexistent_col FROM symbols"),
    ("syntax error", "SELECT FROM WHERE"),
    ("cte then bad table", "WITH w AS (SELECT 1 AS x) SELECT * FROM w JOIN secrets ON 1=1"),
]

fails = 0
print("--- should ALLOW ---")
for label, sql in ALLOW:
    v = sqlguard.validate(sql)
    mark = "ok " if v.ok else "FAIL"
    if not v.ok:
        fails += 1
    print(f"  [{mark}] {label:26} {'' if v.ok else '-> ' + v.reason}")

print("--- should DENY ---")
for label, sql in DENY:
    v = sqlguard.validate(sql)
    mark = "ok " if not v.ok else "FAIL"
    if v.ok:
        fails += 1
    print(f"  [{mark}] {label:26} {v.reason if not v.ok else '!! ALLOWED THROUGH !!'}")

print()
print("read-only enforcement:")
from db import get_readonly_connection
c = get_readonly_connection()
try:
    c.execute("CREATE TABLE should_fail (x INT)")
    print("  [FAIL] read-only connection accepted a write")
    fails += 1
except Exception as e:
    print(f"  [ok ] engine refused the write: {type(e).__name__}: {e}")
finally:
    c.close()

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
