"""SQLite persistence: raw fetched data + derived series.

Raw tables (transactions, cash_movements, products, current_positions) come straight
from DEGIRO. Cached market data (prices, fx_rates) and the derived daily_value series
are kept separate so reconstruction can re-run without re-fetching from DEGIRO.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY,
    date          TEXT,
    product_id    INTEGER,
    buysell       TEXT,
    quantity      REAL,
    price         REAL,
    total         REAL,
    fx_rate       REAL,
    total_in_base_currency        REAL,
    total_plus_all_fees_in_base_currency REAL,
    fee_in_base_currency          REAL,
    raw           TEXT
);

CREATE TABLE IF NOT EXISTS cash_movements (
    id            INTEGER,
    date          TEXT,
    value_date    TEXT,
    product_id    INTEGER,
    type          TEXT,
    description   TEXT,
    currency      TEXT,
    change        REAL,
    balance       TEXT,
    raw           TEXT,
    PRIMARY KEY (id, date, description, change)
);

CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY,
    isin          TEXT,
    symbol        TEXT,
    name          TEXT,
    currency      TEXT,
    product_type  TEXT,
    exchange_id   TEXT,
    close_price   REAL,
    raw           TEXT
);

-- product_id is TEXT because cash positions use string ids (e.g. 'FLATEX_EUR')
-- alongside numeric security ids.
CREATE TABLE IF NOT EXISTS current_positions (
    product_id    TEXT PRIMARY KEY,
    size          REAL,
    price         REAL,
    value         REAL,
    raw           TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    ticker        TEXT,
    date          TEXT,
    close         REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    pair          TEXT,
    date          TEXT,
    rate          REAL,
    PRIMARY KEY (pair, date)
);

CREATE TABLE IF NOT EXISTS daily_value (
    date              TEXT PRIMARY KEY,
    holdings_value    REAL,
    cash              REAL,
    total_value       REAL,
    net_invested      REAL
);

CREATE TABLE IF NOT EXISTS daily_position_value (
    date          TEXT,
    product_id    INTEGER,
    value         REAL,
    PRIMARY KEY (date, product_id)
);

-- One authoritative row per calendar day it was observed (pinned to DEGIRO at sync
-- time). Applied over the reconstruction so past days become exact rather than
-- re-derived from revisable Yahoo prices — important for the Box 3 1-Jan peildatum.
CREATE TABLE IF NOT EXISTS value_snapshots (
    date              TEXT PRIMARY KEY,
    holdings_value    REAL,
    cash              REAL,
    total_value       REAL,
    net_invested      REAL
);

CREATE TABLE IF NOT EXISTS benchmark_prices (
    ticker        TEXT,
    date          TEXT,
    close         REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS upcoming_payments (
    product_id    TEXT,
    product       TEXT,
    currency      TEXT,
    amount        REAL,
    pay_date      TEXT,
    description   TEXT,
    raw           TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def connection(db_path: Path | None = None):
    path = db_path or settings.db_file
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight in-place migrations for older databases."""
    # current_positions.product_id was INTEGER PRIMARY KEY, which rejects string cash
    # ids (e.g. 'FLATEX_EUR'). Recreate it as TEXT. Safe: it is rebuilt each full sync.
    info = conn.execute("PRAGMA table_info(current_positions)").fetchall()
    for col in info:
        if col["name"] == "product_id" and col["type"].upper() == "INTEGER":
            conn.execute("DROP TABLE current_positions")
            conn.executescript(SCHEMA)
            break


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


# ---------------------------------------------------------------------------
# Writers (raw data)
# ---------------------------------------------------------------------------

def save_transactions(conn: sqlite3.Connection, rows: list[dict]) -> None:
    for r in rows:
        conn.execute(
            """INSERT INTO transactions
               (id, date, product_id, buysell, quantity, price, total, fx_rate,
                total_in_base_currency, total_plus_all_fees_in_base_currency,
                fee_in_base_currency, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   date=excluded.date, product_id=excluded.product_id,
                   buysell=excluded.buysell, quantity=excluded.quantity,
                   price=excluded.price, total=excluded.total, fx_rate=excluded.fx_rate,
                   total_in_base_currency=excluded.total_in_base_currency,
                   total_plus_all_fees_in_base_currency=excluded.total_plus_all_fees_in_base_currency,
                   fee_in_base_currency=excluded.fee_in_base_currency, raw=excluded.raw""",
            (
                r.get("id"),
                _as_str(r.get("date")),
                r.get("product_id"),
                r.get("buysell"),
                r.get("quantity"),
                r.get("price"),
                r.get("total"),
                r.get("fx_rate"),
                r.get("total_in_base_currency"),
                r.get("total_plus_all_fees_in_base_currency"),
                r.get("fee_in_base_currency"),
                json.dumps(r, default=str),
            ),
        )


def save_cash_movements(conn: sqlite3.Connection, rows: list[dict]) -> None:
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO cash_movements
               (id, date, value_date, product_id, type, description, currency,
                change, balance, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                r.get("id"),
                _as_str(r.get("date")),
                _as_str(r.get("value_date")),
                r.get("product_id"),
                r.get("type"),
                r.get("description"),
                r.get("currency"),
                r.get("change"),
                json.dumps(r.get("balance"), default=str),
                json.dumps(r, default=str),
            ),
        )


def save_products(conn: sqlite3.Connection, products: dict[int, dict]) -> None:
    for pid, r in products.items():
        conn.execute(
            """INSERT OR REPLACE INTO products
               (id, isin, symbol, name, currency, product_type, exchange_id,
                close_price, raw)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                int(pid),
                r.get("isin"),
                r.get("symbol"),
                r.get("name"),
                r.get("currency"),
                r.get("product_type"),
                _as_str(r.get("exchange_id")),
                _as_float(r.get("close_price")),
                json.dumps(r, default=str),
            ),
        )


def save_current_positions(conn: sqlite3.Connection, positions: list[dict]) -> None:
    conn.execute("DELETE FROM current_positions")
    for p in positions:
        conn.execute(
            "INSERT OR REPLACE INTO current_positions (product_id, size, price, value, raw) "
            "VALUES (?,?,?,?,?)",
            (
                p.get("product_id"),
                _as_float(p.get("size")),
                _as_float(p.get("price")),
                _as_float(p.get("value")),
                json.dumps(p, default=str),
            ),
        )


def save_prices(conn: sqlite3.Connection, ticker: str, series: list[tuple[str, float]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO prices (ticker, date, close) VALUES (?,?,?)",
        [(ticker, d, c) for d, c in series],
    )


def save_benchmark_prices(conn: sqlite3.Connection, ticker: str,
                          series: list[tuple[str, float]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO benchmark_prices (ticker, date, close) VALUES (?,?,?)",
        [(ticker, d, c) for d, c in series],
    )


def save_upcoming_payments(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.execute("DELETE FROM upcoming_payments")
    for r in rows:
        conn.execute(
            "INSERT INTO upcoming_payments (product_id, product, currency, amount, "
            "pay_date, description, raw) VALUES (?,?,?,?,?,?,?)",
            (
                _as_str(r.get("product_id")),
                r.get("product"),
                r.get("currency"),
                _as_float(r.get("amount")),
                _as_str(r.get("pay_date")),
                r.get("description"),
                json.dumps(r, default=str),
            ),
        )


def save_fx_rates(conn: sqlite3.Connection, pair: str, series: list[tuple[str, float]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO fx_rates (pair, date, rate) VALUES (?,?,?)",
        [(pair, d, r) for d, r in series],
    )


def save_daily_value(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    conn.execute("DELETE FROM daily_value")
    df.to_sql("daily_value", conn, if_exists="append", index=False)


def save_value_snapshot(conn: sqlite3.Connection, row: dict) -> None:
    """Upsert one authoritative daily snapshot (keyed by date)."""
    conn.execute(
        """INSERT INTO value_snapshots (date, holdings_value, cash, total_value, net_invested)
           VALUES (?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET
               holdings_value=excluded.holdings_value, cash=excluded.cash,
               total_value=excluded.total_value, net_invested=excluded.net_invested""",
        (row["date"], _as_float(row.get("holdings_value")), _as_float(row.get("cash")),
         _as_float(row.get("total_value")), _as_float(row.get("net_invested"))),
    )


def save_position_values(conn: sqlite3.Connection, rows: list[tuple[str, int, float]]) -> None:
    conn.execute("DELETE FROM daily_position_value")
    conn.executemany(
        "INSERT OR REPLACE INTO daily_position_value (date, product_id, value) VALUES (?,?,?)",
        rows,
    )


# ---------------------------------------------------------------------------
# Readers (used by reconstruction + dashboard)
# ---------------------------------------------------------------------------

def read_df(table: str, db_path: Path | None = None) -> pd.DataFrame:
    with connection(db_path) as conn:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)


def _as_str(value):
    return None if value is None else str(value)


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
