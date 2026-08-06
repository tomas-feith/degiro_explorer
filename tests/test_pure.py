"""Unit tests for pure helpers (no DB)."""

import pandas as pd

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
