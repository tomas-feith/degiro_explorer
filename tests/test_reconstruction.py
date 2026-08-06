"""DB-backed tests for reconstruction, cash-sweep handling, TWR, FIFO, snapshots."""

from datetime import date, timedelta

import pandas as pd

from degiro_explorer import analytics, reconstruct, store


def _seed_basic(buy_day: str):
    """One product, one buy of 10 @ 100 (EUR), a 1000 deposit, prices 200, fx 1."""
    with store.connection() as conn:
        store.save_products(conn, {1: {"isin": "TEST", "symbol": "TESTX", "name": "Test", "currency": "EUR"}})
        store.save_transactions(
            conn,
            [
                {
                    "id": 1,
                    "date": buy_day,
                    "product_id": 1,
                    "buysell": "B",
                    "quantity": 10.0,
                    "price": 100.0,
                    "total": -1000.0,
                    "total_in_base_currency": -1000.0,
                    "total_plus_all_fees_in_base_currency": -1001.0,
                    "fee_in_base_currency": -1.0,
                }
            ],
        )
        store.save_cash_movements(
            conn,
            [
                {
                    "id": 99,
                    "date": buy_day,
                    "value_date": buy_day,
                    "product_id": None,
                    "type": "CASH_TRANSACTION",
                    "description": "flatex Deposit",
                    "currency": "EUR",
                    "change": 1000.0,
                    "balance": {},
                },
                # The purchase debits cash (real data records this as a TRANSACTION row):
                {
                    "id": 101,
                    "date": buy_day,
                    "value_date": buy_day,
                    "product_id": 1,
                    "type": "TRANSACTION",
                    "description": "Compra 10 Test",
                    "currency": "EUR",
                    "change": -1000.0,
                    "balance": {},
                },
                # Internal sweep must NOT count toward cash:
                {
                    "id": 100,
                    "date": buy_day,
                    "value_date": buy_day,
                    "product_id": None,
                    "type": "FLATEX_CASH_SWEEP",
                    "description": "Degiro Cash Sweep Transfer",
                    "currency": "EUR",
                    "change": 950.0,
                    "balance": {},
                },
            ],
        )
        cal = [(date.today() - timedelta(days=d)).isoformat() for d in range(8, -1, -1)]
        store.save_prices(conn, "TESTX", [(d, 200.0) for d in cal])


def test_reconstruction_excludes_cash_sweep(tmp_db):
    buy_day = (date.today() - timedelta(days=5)).isoformat()
    _seed_basic(buy_day)
    daily = reconstruct.build_daily_value("EUR")
    last = daily.iloc[-1]
    assert abs(last["holdings_value"] - 10 * 200.0) < 1e-6  # 2000
    # cash = deposit(1000) - purchase(1000), sweep(+950) excluded
    assert abs(last["cash"] - 0.0) < 1e-6
    assert abs(last["net_invested"] - 1000.0) < 1e-6


def test_snapshot_overrides_reconstruction(tmp_db):
    buy_day = (date.today() - timedelta(days=5)).isoformat()
    _seed_basic(buy_day)
    snap_day = (date.today() - timedelta(days=3)).isoformat()
    with store.connection() as conn:
        store.save_value_snapshot(
            conn,
            {"date": snap_day, "holdings_value": 12345.0, "cash": 5.0, "total_value": 12350.0, "net_invested": 1000.0},
        )
    daily = reconstruct.build_daily_value("EUR").set_index("date")
    assert abs(daily.loc[snap_day, "total_value"] - 12350.0) < 1e-6


def test_todays_snapshot_does_not_freeze_a_same_day_deposit(tmp_db):
    """A snapshot taken earlier today must not lock today's row.

    Regression: an intraday sync wrote a snapshot, then a EUR 1,000 deposit landed. The
    next sync's `_apply_snapshots` restored the pre-deposit net_invested and re-saved it,
    so the deposit vanished permanently and P/L was overstated by exactly that amount.
    `_pin_current_day` hid it for holdings/cash, which is why only net_invested drifted.
    """
    buy_day = (date.today() - timedelta(days=5)).isoformat()
    _seed_basic(buy_day)
    today = date.today().isoformat()
    with store.connection() as conn:
        # Extra deposit today, after the stale snapshot below was captured.
        store.save_cash_movements(
            conn,
            [
                {
                    "id": 102,
                    "date": today,
                    "value_date": today,
                    "product_id": None,
                    "type": "CASH_TRANSACTION",
                    "description": "flatex Deposit",
                    "currency": "EUR",
                    "change": 1000.0,
                    "balance": {},
                }
            ],
        )
        store.save_value_snapshot(
            conn,
            {
                "date": today,
                "holdings_value": 2000.0,
                "cash": 0.0,
                "total_value": 2000.0,
                "net_invested": 1000.0,  # stale: predates the deposit above
            },
        )
    daily = reconstruct.build_daily_value("EUR").set_index("date")
    assert abs(daily.loc[today, "net_invested"] - 2000.0) < 1e-6


def test_twr_is_deposit_proof(tmp_db):
    # Day0 deposit 100, value 100. Day1 +10% (value 110). Day2 deposit +100 (value 210, no market move).
    rows = pd.DataFrame(
        [
            {"date": "2026-01-01", "holdings_value": 100, "cash": 0, "total_value": 100, "net_invested": 100},
            {"date": "2026-01-02", "holdings_value": 110, "cash": 0, "total_value": 110, "net_invested": 100},
            {"date": "2026-01-03", "holdings_value": 210, "cash": 0, "total_value": 210, "net_invested": 200},
        ]
    )
    with store.connection() as conn:
        store.save_daily_value(conn, rows)
    curves = analytics.performance_curves()
    # TWR should reflect the +10% market move only, not the deposit.
    assert abs(curves["twr_pct"].iloc[-1] - 10.0) < 1e-6
    # Naive value/invested would wrongly show +5% (210 vs 200); TWR ignores that.


def test_fifo_realized_gains(tmp_db):
    with store.connection() as conn:
        store.save_products(conn, {1: {"isin": "T", "symbol": "T", "name": "T", "currency": "EUR"}})
        store.save_transactions(
            conn,
            [
                {
                    "id": 1,
                    "date": "2026-01-01",
                    "product_id": 1,
                    "buysell": "B",
                    "quantity": 10.0,
                    "price": 100.0,
                    "total": -1000.0,
                    "total_in_base_currency": -1000.0,
                    "total_plus_all_fees_in_base_currency": -1000.0,
                    "fee_in_base_currency": 0.0,
                },
                {
                    "id": 2,
                    "date": "2026-02-01",
                    "product_id": 1,
                    "buysell": "S",
                    "quantity": -4.0,
                    "price": 150.0,
                    "total": 600.0,
                    "total_in_base_currency": 600.0,
                    "total_plus_all_fees_in_base_currency": 600.0,
                    "fee_in_base_currency": 0.0,
                },
            ],
        )
    rg = analytics.realized_gains()
    assert len(rg) == 1
    # Sold 4 @150 = 600 proceeds; FIFO cost 4 @100 = 400; gain 200.
    assert abs(rg.iloc[0]["gain"] - 200.0) < 1e-6
