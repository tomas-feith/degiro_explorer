"""Tests for the legacy-database migration in store._migrate.

Rarely exercised (it only fires on a pre-existing old DB) and silent when wrong, which
is exactly why it needs a test rather than a manual check.
"""

import sqlite3

from degiro_explorer import store

# The shape current_positions had before cash positions forced product_id to TEXT.
LEGACY_SCHEMA = """
CREATE TABLE current_positions (
    product_id INTEGER PRIMARY KEY,
    size REAL,
    price REAL,
    value REAL
);
"""


def _product_id_type(conn: sqlite3.Connection) -> str:
    info = conn.execute("PRAGMA table_info(current_positions)").fetchall()
    return next(c["type"].upper() for c in info if c["name"] == "product_id")


def test_migrate_converts_integer_pk_to_text(tmp_db):
    """An old INTEGER-PK DB must end up TEXT so cash ids like FLATEX_EUR fit."""
    with store.connection() as conn:
        conn.execute("DROP TABLE current_positions")
        conn.executescript(LEGACY_SCHEMA)
        assert _product_id_type(conn) == "INTEGER"

    store.init_db()

    with store.connection() as conn:
        assert _product_id_type(conn) == "TEXT"
        # The whole point: string cash ids must now insert without error.
        store.save_current_positions(conn, [{"product_id": "FLATEX_EUR", "size": 1096.87, "value": 1096.87}])
        stored = conn.execute("SELECT product_id FROM current_positions").fetchall()
        assert [r["product_id"] for r in stored] == ["FLATEX_EUR"]


def test_migrate_is_idempotent_on_a_current_db(tmp_db):
    """Re-running against an already-TEXT DB must not drop or rebuild anything."""
    with store.connection() as conn:
        store.save_current_positions(conn, [{"product_id": "FLATEX_EUR", "size": 1.0, "value": 1.0}])

    store.init_db()
    store.init_db()

    with store.connection() as conn:
        assert _product_id_type(conn) == "TEXT"
        rows = conn.execute("SELECT product_id FROM current_positions").fetchall()
        assert [r["product_id"] for r in rows] == ["FLATEX_EUR"]
