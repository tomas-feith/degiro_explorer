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
    products = pd.DataFrame([
        {"id": 1, "isin": "US0378331005", "symbol": "XXX", "currency": "USD", "name": "Apple"},
        {"id": 2, "isin": "NL0000000000", "symbol": None, "currency": "EUR", "name": "NoSymbol"},
        {"id": 3, "isin": "IEXXXX", "symbol": "VWRL", "currency": "GBP", "name": "GBP fund"},
    ])
    mapping, unresolved = prices.resolve_tickers(products)
    assert mapping[1] == "AAPL"            # ISIN override wins over symbol
    assert mapping[3] == "VWRL.L"          # GBP -> .L suffix heuristic
    assert 1 not in [u["id"] for u in unresolved]
    assert 2 in [u["id"] for u in unresolved]  # NaN symbol -> unresolved (not "nan")
