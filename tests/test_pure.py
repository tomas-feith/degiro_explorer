"""Unit tests for pure helpers (no DB)."""

import pandas as pd
import pytest

from degiro_explorer import analytics, prices, reports


def test_box3_tax_below_allowance():
    r = analytics.box3_tax(value=34000, deemed_return_pct=5.88, allowance=51396, rate_pct=36)
    assert r["taxable_base"] == 0
    assert r["tax"] == 0


def test_box3_tax_above_allowance():
    r = analytics.box3_tax(value=120000, deemed_return_pct=5.88, allowance=51396, rate_pct=36)
    assert round(r["taxable_base"], 2) == 68604.0
    assert round(r["deemed_income"], 2) == 4033.92
    assert round(r["tax"], 2) == 1452.21


def test_box3_params_enacted_2026_figures():
    """2026 was enacted at 6.00% / EUR 59,357 after the proposed 7.78% hike was dropped."""
    p = analytics.box3_params(2026)
    assert p.deemed_return_pct == 6.00
    assert p.allowance == 59357.0
    assert p.rate_pct == 36.0


def test_box3_params_unknown_year_falls_back_to_latest():
    assert analytics.box3_params(2099) is analytics.BOX3_PARAMS[analytics.LATEST_BOX3_YEAR]
    assert analytics.box3_params(1999) is analytics.BOX3_PARAMS[analytics.LATEST_BOX3_YEAR]


def test_box3_params_2027_is_flagged_provisional():
    """2027 figures are announced, not enacted, and the allowance is a placeholder."""
    p = analytics.box3_params(2027)
    assert p.deemed_return_pct == 6.37
    assert p.provisional is True
    assert analytics.box3_params(2026).provisional is False


def test_drop_closed_removes_sold_out_positions():
    """DEGIRO leaves a size-0 row for a product sold today; it must not show as a holding."""
    pos = pd.DataFrame(
        [
            {"product_id": 1, "size": 21.0, "value": 15141.42},
            {"product_id": 2, "size": 0.0, "value": 0.0},  # sold out today
            {"product_id": 3, "size": -5.0, "value": -250.0},  # short: still a position
        ]
    )
    kept = analytics._drop_closed(pos)
    assert sorted(kept["product_id"]) == [1, 3]


def test_holdings_classification_separates_asset_class_from_category(monkeypatch):
    """A bond fund is core/Global too, so asset_class must be its own axis or bonds
    are indistinguishable from global equity in the allocation breakdowns."""
    monkeypatch.setattr(
        analytics,
        "current_securities",
        lambda: pd.DataFrame(
            [
                {"isin": "IE00BDBRDM35", "name": "Global Agg Bond", "value": 2622.41, "weight": 30.0},
                {"isin": "IE00BF4RFH31", "name": "World Small Cap", "value": 2256.17, "weight": 70.0},
            ]
        ),
    )
    monkeypatch.setattr(
        analytics,
        "_holdings_meta",
        lambda: {
            "IE00BDBRDM35": {"asset_class": "Bonds", "category": "core", "region": "Global", "ter": 0.10},
            "IE00BF4RFH31": {"asset_class": "Equity", "category": "core", "region": "Global", "ter": 0.35},
        },
    )
    df = analytics.holdings_classification()
    # Both are "core" and "Global" -- only asset_class tells them apart.
    assert set(df["category"]) == {"core"}
    assert set(df["region"]) == {"Global"}
    assert sorted(df["asset_class"]) == ["Bonds", "Equity"]


def test_holdings_classification_defaults_unknown_asset_class(monkeypatch):
    """An unmapped holding must fall back, not raise, so a new buy still renders."""
    monkeypatch.setattr(
        analytics,
        "current_securities",
        lambda: pd.DataFrame([{"isin": "IE00NEW", "name": "Unmapped", "value": 100.0, "weight": 100.0}]),
    )
    monkeypatch.setattr(analytics, "_holdings_meta", lambda: {})
    df = analytics.holdings_classification()
    assert df["asset_class"].iloc[0] == "unknown"
    assert df["category"].iloc[0] == "unclassified"
    assert pd.isna(df["ter"].iloc[0])


def test_benchmark_curves_ignores_deconfigured_tickers(monkeypatch):
    """benchmark_prices keeps rows for every ticker ever configured, so a benchmark
    removed from tickers.yml must stop being plotted rather than linger as a curve."""
    dates = pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12"])
    monkeypatch.setattr(analytics, "daily_value", lambda: pd.DataFrame({"date": dates, "total_value": [1.0, 2.0, 3.0]}))
    monkeypatch.setattr(
        analytics.store,
        "read_df",
        lambda table: pd.DataFrame(
            {
                "ticker": ["SPYI.DE"] * 3 + ["IWDA.AS"] * 3,
                "date": list(dates) * 2,
                "close": [100.0, 110.0, 121.0, 50.0, 55.0, 60.0],
            }
        ),
    )
    monkeypatch.setattr(analytics.prices, "load_benchmarks", lambda: ["SPYI.DE"])
    curves = analytics.benchmark_curves()
    assert set(curves["benchmark"]) == {"SPYI.DE"}  # the retired IWDA.AS rows are ignored
    assert round(curves["return_pct"].iloc[-1], 2) == 21.0


def test_benchmark_curves_empty_when_nothing_configured(monkeypatch):
    """No configured benchmarks must yield an empty frame, not every stored ticker."""
    dates = pd.to_datetime(["2026-08-10", "2026-08-11"])
    monkeypatch.setattr(analytics, "daily_value", lambda: pd.DataFrame({"date": dates, "total_value": [1.0, 2.0]}))
    monkeypatch.setattr(
        analytics.store,
        "read_df",
        lambda table: pd.DataFrame({"ticker": ["IWDA.AS"] * 2, "date": list(dates), "close": [50.0, 55.0]}),
    )
    monkeypatch.setattr(analytics.prices, "load_benchmarks", lambda: [])
    assert analytics.benchmark_curves().empty


def test_to_float_locale_parsing():
    # comma decimals, NBSP/Â thousands separators, quoting
    assert reports._to_float("1096,87") == 1096.87
    assert reports._to_float("-4008,50") == -4008.50
    assert reports._to_float("6Â 115,78") == 6115.78
    assert reports._to_float("10.000,00") == 10000.0
    assert reports._to_float("") is None
    assert reports._to_float(None) is None


def test_resolve_tickers_handles_nan_and_override(monkeypatch):
    monkeypatch.setattr(prices, "_load_overrides", lambda: {"US0378331005": "AAPL"})
    products = pd.DataFrame(
        [
            {"id": 1, "isin": "US0378331005", "symbol": "XXX", "currency": "USD", "name": "Apple"},
            {"id": 2, "isin": "NL0000000000", "symbol": None, "currency": "EUR", "name": "NoSymbol"},
            {"id": 3, "isin": "IEXXXX", "symbol": "VWRL", "currency": "GBP", "name": "GBP fund"},
        ]
    )
    mapping, unresolved = prices.resolve_tickers(products)
    assert mapping[1] == "AAPL"  # ISIN override wins over symbol
    assert mapping[3] == "VWRL.L"  # GBP -> .L suffix heuristic
    assert 1 not in [u["id"] for u in unresolved]
    assert 2 in [u["id"] for u in unresolved]  # NaN symbol -> unresolved (not "nan")


def test_resolve_start_year_uses_earliest_transaction(tmp_db, monkeypatch):
    from config import settings
    from degiro_explorer import fetch, store

    monkeypatch.setattr(settings, "start_year", None)
    with store.connection() as conn:
        store.save_transactions(
            conn,
            [
                {"id": 1, "date": "2026-04-07 08:16:10+02:00", "product_id": 10, "quantity": 1},
                {"id": 2, "date": "2026-06-25 07:48:41+02:00", "product_id": 10, "quantity": 1},
            ],
        )
    assert fetch.resolve_start_year() == 2026


def test_resolve_start_year_spans_cash_movements(tmp_db, monkeypatch):
    """The opening deposit predates the first trade, and bounds the same fetch."""
    from config import settings
    from degiro_explorer import fetch, store

    monkeypatch.setattr(settings, "start_year", None)
    with store.connection() as conn:
        store.save_transactions(conn, [{"id": 1, "date": "2026-01-15 10:00:00+01:00", "product_id": 10, "quantity": 1}])
        store.save_cash_movements(
            conn,
            [
                {
                    "id": 1,
                    "date": "2025-12-20 09:00:00+01:00",
                    "type": "CASH_TRANSACTION",
                    "description": "Depositos",
                    "currency": "EUR",
                    "change": 5000.0,
                }
            ],
        )
    # Must be 2025 (the deposit), not 2026 (the first trade), or that cash is lost.
    assert fetch.resolve_start_year() == 2025


def test_resolve_start_year_falls_back_on_empty_db(tmp_db, monkeypatch):
    from config import settings
    from degiro_explorer import fetch

    monkeypatch.setattr(settings, "start_year", None)
    assert fetch.resolve_start_year() == fetch.DEFAULT_START_YEAR


def test_resolve_start_year_setting_wins(tmp_db, monkeypatch):
    from config import settings
    from degiro_explorer import fetch

    monkeypatch.setattr(settings, "start_year", 2020)
    assert fetch.resolve_start_year() == 2020


def test_position_performance_uses_average_cost_of_shares_still_held(tmp_db):
    """A partly sold-down holding must not net sale proceeds against its cost basis.

    Netting made IQQY read +45% (basis EUR 1,999 vs EUR 2,908 of value) when the app
    showed +5%: the realised gain had been subtracted from the cost of the shares still
    held. The basis is the moving-average cost of the remaining shares, DEGIRO-style.
    """
    from degiro_explorer import analytics, store

    def tx(tid: int, day: str, qty: float, total: float) -> dict:
        return {
            "id": tid,
            "date": f"2026-{day}T08:00:00+02:00",
            "product_id": 7,
            "buysell": "B" if qty > 0 else "S",
            "quantity": qty,
            "price": abs(total / qty),
            "total": total,
            "total_in_base_currency": total,
            "total_plus_all_fees_in_base_currency": total,
            "fee_in_base_currency": 0.0,
        }

    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "IE00B1YZSC51", "symbol": "IQQY", "name": "Europe", "currency": "EUR"}})
        store.save_transactions(
            conn,
            [
                tx(1, "04-07", 100.0, -3700.0),  # avg cost 38.50 over the two buys
                tx(2, "06-25", 100.0, -4000.0),
                tx(3, "08-18", -150.0, 6300.0),  # sold at 42.00 -> realised, basis untouched
            ],
        )
        store.save_position_values(conn, [("2026-08-21", 7, 2100.0)])  # 50 shares @ 42.00

    row = analytics.position_performance().iloc[0]
    assert row["cost"] == pytest.approx(50 * 38.50)
    assert row["pnl"] == pytest.approx(2100.0 - 1925.0)
    assert row["return_pct"] == pytest.approx((42.0 / 38.50 - 1) * 100)


def test_position_performance_drops_a_fully_closed_holding(tmp_db):
    from degiro_explorer import analytics, store

    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "X", "symbol": "X", "name": "Gone", "currency": "EUR"}})
        store.save_transactions(
            conn,
            [
                {
                    "id": 1,
                    "date": "2026-04-07T08:00:00+02:00",
                    "product_id": 7,
                    "buysell": "B",
                    "quantity": 10.0,
                    "price": 10.0,
                    "total": -100.0,
                    "total_in_base_currency": -100.0,
                    "total_plus_all_fees_in_base_currency": -100.0,
                    "fee_in_base_currency": 0.0,
                },
                {
                    "id": 2,
                    "date": "2026-05-07T08:00:00+02:00",
                    "product_id": 7,
                    "buysell": "S",
                    "quantity": -10.0,
                    "price": 12.0,
                    "total": 120.0,
                    "total_in_base_currency": 120.0,
                    "total_plus_all_fees_in_base_currency": 120.0,
                    "fee_in_base_currency": 0.0,
                },
            ],
        )
        store.save_position_values(conn, [("2026-08-21", 7, 0.0)])

    assert analytics.position_performance().empty


def test_pnl_reconciliation_bridges_the_transaction_ledger_to_total_pnl(tmp_db):
    """Total P/L exceeds realised + unrealised by exactly the non-trade cash income.

    Dividends and rebates never touch a transaction row, so the Transactions tab's
    Combined figure is legitimately below the Overview's Total P/L; the bridge must
    account for the gap rather than leave it looking like a mismatch.
    """
    from degiro_explorer import analytics, store

    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "X", "symbol": "X", "name": "Fund", "currency": "EUR"}})
        store.save_transactions(
            conn,
            [
                {
                    "id": 1,
                    "date": "2026-04-07T08:00:00+02:00",
                    "product_id": 7,
                    "buysell": "B",
                    "quantity": 100.0,
                    "price": 10.0,
                    "total": -1000.0,
                    "total_in_base_currency": -1000.0,
                    "total_plus_all_fees_in_base_currency": -1000.0,
                    "fee_in_base_currency": 0.0,
                },
            ],
        )
        store.save_cash_movements(
            conn,
            [
                {
                    "id": 1,
                    "date": "2026-06-10T09:00:00+02:00",
                    "type": "CASH_TRANSACTION",
                    "description": "Dividendo",
                    "currency": "EUR",
                    "change": 20.0,
                },
                {
                    "id": 2,
                    "date": "2026-06-11T09:00:00+02:00",
                    "type": "CASH_TRANSACTION",
                    "description": "DEGIRO Rebate Promotion",
                    "currency": "EUR",
                    "change": 5.0,
                },
            ],
        )
        store.save_current_positions(conn, [{"product_id": 7, "size": 100.0, "price": 11.0, "value": 1100.0}])
        store.save_daily_value(
            conn,
            pd.DataFrame(
                [
                    {
                        "date": "2026-08-21",
                        "holdings_value": 1100.0,
                        "cash": 25.0,
                        "total_value": 1125.0,
                        "net_invested": 1000.0,
                    }
                ]
            ),
        )

    rec = analytics.pnl_reconciliation()
    assert rec["total_pnl"] == pytest.approx(125.0)
    assert rec["realized"] == pytest.approx(0.0)
    assert rec["unrealized"] == pytest.approx(100.0)  # 100 shares marked 10 -> 11
    assert rec["dividends"] == pytest.approx(20.0)
    assert rec["other"] == pytest.approx(5.0)  # the rebate, not a silent residual


def test_position_return_history_does_not_spike_on_a_sell_down(tmp_db):
    """The over-time plot shares the per-holding basis, so it must not spike on a sale.

    It had its own copy of the netting bug: cumulative cost ran as buys minus sale
    proceeds, so the day IQQY was sold down the curve jumped from ~6% to ~45%.
    """
    from degiro_explorer import analytics, store

    def tx(tid: int, day: str, qty: float, total: float) -> dict:
        return {
            "id": tid,
            "date": f"2026-{day}T08:00:00+02:00",
            "product_id": 7,
            "buysell": "B" if qty > 0 else "S",
            "quantity": qty,
            "price": abs(total / qty),
            "total": total,
            "total_in_base_currency": total,
            "total_plus_all_fees_in_base_currency": total,
            "fee_in_base_currency": 0.0,
        }

    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "X", "symbol": "X", "name": "Fund", "currency": "EUR"}})
        store.save_transactions(
            conn,
            [
                tx(1, "04-07", 100.0, -3850.0),  # 100 @ 38.50
                tx(2, "08-18", -50.0, 2100.0),  # sold half at 42.00
            ],
        )
        store.save_position_values(
            conn,
            [
                ("2026-08-17", 7, 4100.0),  # 100 shares @ 41.00
                ("2026-08-18", 7, 2100.0),  # 50 left @ 42.00
                ("2026-08-19", 7, 2100.0),
            ],
        )

    hist = analytics.position_return_history().set_index("date")["return_pct"]
    assert hist.loc["2026-08-17"] == pytest.approx((41.0 / 38.50 - 1) * 100)
    # Basis halves with the position: the return tracks price, it does not jump.
    assert hist.loc["2026-08-18"] == pytest.approx((42.0 / 38.50 - 1) * 100)
    assert hist.max() < 15.0


# --- locale-agnostic report parsing -------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-4008,50", -4008.50),  # pt_PT decimal comma
        ("6\xa0115,78", 6115.78),  # NBSP thousands
        ("1.234,56", 1234.56),  # pt_PT with dot thousands
        ("1,234.56", 1234.56),  # en_US
        ("0.35", 0.35),  # en_US decimal point -- was parsed as 35
        ("1.234", 1234.0),  # three trailing digits -> thousands
        ("12", 12.0),
        ("", None),
        ("n/a", None),
    ],
)
def test_report_number_parsing_follows_the_separators_present(raw, expected):
    """The reports are requested with lang="en" while the account is pt_PT.

    Assuming a comma decimal made '0.35' parse as 35 -- every figure 100x out, with no
    error to notice.
    """
    assert reports._to_float(raw) == expected


# --- pence-quoted (GBX) listings ----------------------------------------------


def test_gbx_is_quoted_in_pence_and_converted_via_gbp():
    """A London listing quotes pence against a GBX product currency.

    Without the /100 and the GBP redirect there is no "GBXEUR" pair to find, the FX
    lookup yields nothing and the holding values at zero.
    """
    assert prices.quote_adjustment("GBX") == ("GBP", 100.0)
    assert prices.quote_adjustment("GBp") == ("GBP", 100.0)
    assert prices.quote_adjustment("EUR") == ("EUR", 1.0)
    assert prices.quote_adjustment(None) == ("", 1.0)


def test_backfill_fx_asks_for_gbp_not_gbx(monkeypatch, tmp_db):
    import datetime

    asked: list[str] = []

    def record(ticker, start, end, **kwargs):
        asked.append(ticker)
        return []

    monkeypatch.setattr(prices, "_download_close", record)
    prices.backfill_fx({"GBX", "EUR"}, "EUR", datetime.date(2026, 1, 1), datetime.date(2026, 2, 1))
    assert asked == ["GBPEUR=X"]


def test_holdings_prices_are_unadjusted_and_benchmarks_are_total_return(monkeypatch, tmp_db):
    """auto_adjust back-adjusts for dividends: right for a benchmark, wrong for holdings.

    An adjusted holding price counts the dividend a second time (it is already in cash)
    and silently rewrites every past close each time a new distribution goes ex.
    """
    import datetime

    seen: dict[str, bool] = {}

    def fake(ticker, start, end, auto_adjust=False):
        seen[ticker] = auto_adjust
        return []

    monkeypatch.setattr(prices, "_download_close", fake)
    monkeypatch.setattr(prices, "load_benchmarks", lambda: ["BENCH.DE"])
    prices.backfill_prices({1: "HOLD.DE"}, datetime.date(2026, 1, 1), datetime.date(2026, 2, 1))
    prices.backfill_benchmarks(datetime.date(2026, 1, 1), datetime.date(2026, 2, 1))
    assert seen["HOLD.DE"] is False
    assert seen["BENCH.DE"] is True


# --- DEGIRO timestamps across a DST change ------------------------------------


def _dst_transactions() -> list[dict]:
    """Two trades either side of the October DST change: +02:00 then +01:00."""
    return [
        {
            "id": 1,
            "date": "2026-06-25T08:00:00+02:00",
            "product_id": 7,
            "buysell": "B",
            "quantity": 10.0,
            "price": 10.0,
            "total": -100.0,
            "total_in_base_currency": -100.0,
            "total_plus_all_fees_in_base_currency": -100.0,
            "fee_in_base_currency": 0.0,
        },
        {
            "id": 2,
            "date": "2026-11-10T08:00:00+01:00",
            "product_id": 7,
            "buysell": "B",
            "quantity": 5.0,
            "price": 12.0,
            "total": -60.0,
            "total_in_base_currency": -60.0,
            "total_plus_all_fees_in_base_currency": -60.0,
            "fee_in_base_currency": 0.0,
        },
    ]


def test_transaction_ledger_survives_a_dst_change(tmp_db):
    """DEGIRO stamps local offsets, so a winter trade mixes +01:00 with summer's +02:00.

    pandas raises "Mixed timezones detected" on a bare to_datetime, which took out the
    Transactions tab and — via the same call in sync.py — the whole sync.
    """
    from degiro_explorer import analytics, store

    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "X", "symbol": "X", "name": "Fund", "currency": "EUR"}})
        store.save_transactions(conn, _dst_transactions())

    ledger = analytics.transactions()
    assert len(ledger) == 2
    assert ledger["date"].is_monotonic_decreasing  # newest first, so both parsed


def test_sync_start_date_survives_a_dst_change(tmp_db):
    import sync

    from degiro_explorer import store

    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "X", "symbol": "X", "name": "Fund", "currency": "EUR"}})
        store.save_transactions(conn, _dst_transactions())

    assert sync._earliest_transaction_date(store.read_df("transactions")).isoformat() == "2026-06-25"


# --- cash-movement duplicates --------------------------------------------------


def _sweep(idx: int) -> dict:
    """A FLATEX_CASH_SWEEP row exactly as DEGIRO sends it: no `change` value."""
    return {
        "id": idx,
        "date": "2026-04-07 11:49:16+02:00",
        "type": "FLATEX_CASH_SWEEP",
        "description": "Levantamentos da sua Conta Caixa",
        "currency": "EUR",
        "change": None,
    }


def test_null_change_movements_do_not_duplicate_on_re_sync(tmp_db):
    """`change` is in the primary key and SQLite treats NULLs as distinct.

    Every sync therefore appended another copy of each NULL-change sweep row: 268 rows
    for 71 real movements before this fix.
    """
    from degiro_explorer import store

    for _ in range(3):  # three syncs of the same data
        with store.connection() as conn:
            store.save_cash_movements(conn, [_sweep(1), _sweep(2)])

    assert len(store.read_df("cash_movements")) == 2


def test_migration_collapses_pre_existing_duplicates(tmp_db):
    """An existing database already carries the duplicates; init_db must clean them."""
    from degiro_explorer import store

    with store.connection() as conn:
        for _ in range(5):
            conn.execute(
                "INSERT INTO cash_movements (id, date, type, description, currency, change) VALUES (?,?,?,?,?,NULL)",
                (1, "2026-04-07 11:49:16+02:00", "FLATEX_CASH_SWEEP", "Levantamentos", "EUR"),
            )
    assert len(store.read_df("cash_movements")) == 5

    store.init_db()
    rows = store.read_df("cash_movements")
    assert len(rows) == 1
    assert rows.iloc[0]["change"] == 0.0


# --- shared derivations --------------------------------------------------------


def test_realized_gains_and_transaction_pnl_come_from_one_walk(tmp_db):
    """realized_gains used to re-implement FIFO. One rule, one implementation."""
    from degiro_explorer import analytics, store

    def tx(tid, day, qty, total):
        return {
            "id": tid,
            "date": f"2026-{day}T08:00:00+02:00",
            "product_id": 7,
            "buysell": "B" if qty > 0 else "S",
            "quantity": qty,
            "price": abs(total / qty),
            "total": total,
            "total_in_base_currency": total,
            "total_plus_all_fees_in_base_currency": total,
            "fee_in_base_currency": 0.0,
        }

    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "X", "symbol": "X", "name": "Fund", "currency": "EUR"}})
        store.save_transactions(conn, [tx(1, "04-07", 100.0, -1000.0), tx(2, "05-07", -40.0, 480.0)])

    gains = analytics.realized_gains()
    assert len(gains) == 1
    row = gains.iloc[0]
    assert row["proceeds"] == pytest.approx(480.0)
    assert row["cost"] == pytest.approx(400.0)
    assert row["gain"] == pytest.approx(80.0)
    assert row["unmatched_quantity"] == 0.0
    per_tx = analytics.transaction_pnl()
    assert per_tx["realized"].sum() == pytest.approx(gains["gain"].sum())


def test_cached_reads_serves_repeat_reads_from_one_query(tmp_db):
    """A dashboard render re-read whole tables 55 times through 55 connections."""
    from degiro_explorer import store

    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "X", "symbol": "X", "name": "Fund", "currency": "EUR"}})

    calls = {"n": 0}
    real_connection = store.connection

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real_connection(*args, **kwargs)

    store.connection = counting
    try:
        with store.cached_reads():
            first = store.read_df("products")
            first.loc[0, "name"] = "mutated"  # callers mutate what they get back
            second = store.read_df("products")
        assert calls["n"] == 1
        assert second.loc[0, "name"] == "Fund"
        # Outside the block every read hits the database again.
        store.read_df("products")
        store.read_df("products")
        assert calls["n"] == 3
    finally:
        store.connection = real_connection


def test_ticker_price_check_flags_a_series_that_does_not_match_your_trades(tmp_db):
    """A resolved ticker is not a correct one.

    Auto-resolution has silently matched a different security on another exchange and
    backfilled plausible-looking prices; only comparing them to a trade price catches it.
    """
    from degiro_explorer import analytics, store

    def tx(tid, pid, price):
        return {
            "id": tid,
            "date": "2026-04-07T08:00:00+02:00",
            "product_id": pid,
            "buysell": "B",
            "quantity": 1.0,
            "price": price,
            "total": -price,
            "total_in_base_currency": -price,
            "total_plus_all_fees_in_base_currency": -price,
            "fee_in_base_currency": 0.0,
        }

    with store.connection() as conn:
        store.save_products(
            conn,
            {
                1: {"isin": "IE_GOOD", "symbol": "GOOD", "name": "Right", "currency": "EUR"},
                2: {"isin": "IE_BAD", "symbol": "BAD", "name": "Wrong exchange", "currency": "EUR"},
            },
        )
        store.save_transactions(conn, [tx(1, 1, 100.0), tx(2, 2, 100.0)])
        store.save_prices(conn, "GOOD", [("2026-04-07", 100.5)])  # intraday drift
        store.save_prices(conn, "BAD", [("2026-04-07", 51.0)])  # another security

    check = analytics.ticker_price_check().set_index("ticker")
    assert check.loc["GOOD", "status"] == "ok"
    assert check.loc["BAD", "status"] == "MISMATCH"
    assert check.loc["BAD", "worst_gap_pct"] == pytest.approx(-49.0)


def test_dividends_in_a_foreign_currency_are_converted_before_totalling(tmp_db):
    """`amount` stays in the movement's currency; only `amount_base` may be summed."""
    from degiro_explorer import analytics, store

    with store.connection() as conn:
        store.save_cash_movements(
            conn,
            [
                {
                    "id": 1,
                    "date": "2026-06-10T09:00:00+02:00",
                    "type": "CASH_TRANSACTION",
                    "description": "Dividendo",
                    "currency": "EUR",
                    "change": 20.0,
                },
                {
                    "id": 2,
                    "date": "2026-06-11T09:00:00+02:00",
                    "type": "CASH_TRANSACTION",
                    "description": "Dividend",
                    "currency": "USD",
                    "change": 10.0,
                },
            ],
        )
        store.save_fx_rates(conn, "USDEUR", [("2026-06-11", 0.9)])

    div = analytics.dividends()
    assert div["amount"].sum() == pytest.approx(30.0)  # unlike units, not a real total
    assert div["amount_base"].sum() == pytest.approx(29.0)  # 20 EUR + 10 USD @ 0.9
