"""End-to-end sync: fetch from DEGIRO -> SQLite -> backfill prices -> reconstruct.

Usage:
    python scripts/sync.py [--start-year YYYY]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Allow running as a plain script: add project root to sys.path.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from degiro_explorer import fetch, prices, reconstruct, store  # noqa: E402
from degiro_explorer.client import connect  # noqa: E402
from degiro_explorer.fetch import DEFAULT_START_YEAR  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync")

REPORTS_DIR = ROOT / "data" / "reports"


def _earliest_transaction_date(tx_df: pd.DataFrame) -> date:
    """First trade date.

    utc=True is REQUIRED: DEGIRO stamps each trade with the local offset, so a history
    spanning a DST change carries both +02:00 and +01:00 and a bare to_datetime raises
    "Mixed timezones detected" -- which would abort the sync before reconstruction.
    """
    return pd.to_datetime(tx_df["date"], utc=True).min().date()


def _fetch_reports(session) -> None:
    """Pull official DEGIRO CSV reports and save them under data/reports/."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today()
    tx_df = store.read_df("transactions")
    start = _earliest_transaction_date(tx_df) if not tx_df.empty else date(today.year, 1, 1)
    try:
        account_csv = fetch.fetch_account_report(session, start, today)
        if account_csv:
            (REPORTS_DIR / "account_report.csv").write_text(account_csv, encoding="utf-8")
        position_csv = fetch.fetch_position_report(session, today)
        if position_csv:
            (REPORTS_DIR / "position_report.csv").write_text(position_csv, encoding="utf-8")
        with store.connection() as conn:
            store.set_meta(conn, "reports_dir", str(REPORTS_DIR))
            store.set_meta(conn, "reports_fetched", today.isoformat())
    except Exception:  # noqa: BLE001 - reports are a nice-to-have, never fail the sync
        logger.warning("Could not fetch official reports", exc_info=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync DEGIRO data and rebuild history.")
    parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        help="Earliest year to pull. Default: derived from the earliest stored "
        f"transaction, or {DEFAULT_START_YEAR} on an empty database.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip DEGIRO login; re-run price backfill + reconstruction "
        "from already-stored data (use after editing tickers.yml).",
    )
    args = parser.parse_args()

    store.init_db()

    if args.offline:
        with store.connection() as conn:
            base_currency = store.get_meta(conn, "base_currency", "EUR")
        logger.info("Offline mode: reusing stored data (base_currency=%s).", base_currency)
    else:
        session = connect()
        base_currency = session.base_currency

        start_year = args.start_year or fetch.resolve_start_year()
        logger.info("Pulling history from %d onwards.", start_year)

        # 1. Raw data from DEGIRO
        transactions = fetch.fetch_transactions(session, start_year=start_year)
        cash_movements = fetch.fetch_cash_movements(session, start_year=start_year)
        product_ids = {t.get("product_id") for t in transactions if t.get("product_id") is not None}
        product_ids |= {m.get("product_id") for m in cash_movements if m.get("product_id")}
        positions = fetch.fetch_current_portfolio(session)
        product_ids |= {p.get("product_id") for p in positions if p.get("product_id")}
        products = fetch.fetch_products(session, [pid for pid in product_ids if pid is not None])

        with store.connection() as conn:
            store.save_transactions(conn, transactions)
            store.save_cash_movements(conn, cash_movements)
            store.save_products(conn, products)
            store.save_current_positions(conn, positions)
            store.set_meta(conn, "last_sync", date.today().isoformat())
            store.set_meta(conn, "base_currency", base_currency)

        # Official DEGIRO reports (for cross-checking + your tax records).
        _fetch_reports(session)

        # Upcoming dividend/coupon payments (best-effort).
        payments = fetch.fetch_upcoming_payments(session)
        with store.connection() as conn:
            store.save_upcoming_payments(conn, payments)

    # 2. Resolve tickers + backfill prices and FX
    products_df = store.read_df("products")
    mapping, unresolved = prices.resolve_tickers(products_df)
    if unresolved:
        logger.warning("Unresolved tickers (%d) — add them to tickers.yml:", len(unresolved))
        for u in unresolved:
            logger.warning("  id=%s isin=%s symbol=%s name=%s", u["id"], u["isin"], u["symbol"], u["name"])

    tx_df = store.read_df("transactions")
    if tx_df.empty:
        logger.warning("No transactions — skipping price backfill and reconstruction.")
        return 0

    start = _earliest_transaction_date(tx_df)
    end = date.today()

    prices.backfill_prices(mapping, start, end)
    currencies = set(products_df["currency"].dropna().unique())
    currencies |= set(store.read_df("cash_movements")["currency"].dropna().unique())
    prices.backfill_fx(currencies, base_currency, start, end)
    prices.backfill_benchmarks(start, end)

    # 3. Reconstruct daily value series + lock in today's snapshot
    daily = reconstruct.build_daily_value(base_currency)
    with store.connection() as conn:
        store.save_daily_value(conn, daily)
        if not daily.empty:
            # Persist today's (DEGIRO-pinned) row so it stays exact in future rebuilds.
            store.save_value_snapshot(conn, daily.iloc[-1].to_dict())

    # 4. Sanity check
    from degiro_explorer import analytics

    if not daily.empty:
        recon_total = float(daily.iloc[-1]["total_value"])
        recon_holdings = float(daily.iloc[-1]["holdings_value"])
        delta = analytics.reconstruction_delta(recon_holdings)
        logger.info("Reconstructed total value today: %.2f %s", recon_total, base_currency)
        logger.info(
            "Holdings sanity check: reconstructed=%.2f vs DEGIRO=%.2f (delta=%.2f, %.1f%%)",
            delta["reconstructed"],
            delta["actual_holdings"],
            delta["delta"],
            delta["delta_pct"],
        )
        if abs(delta["delta_pct"]) > 5:
            logger.warning("Large delta — check unresolved tickers / FX rates above.")

    logger.info("Sync complete. Run: streamlit run dashboard/app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
