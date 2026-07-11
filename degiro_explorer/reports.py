"""Parse DEGIRO's official CSV reports and cross-check them against our figures.

The reports are pulled during a full sync (see scripts/sync.py) and saved under
data/reports/. Numbers use the account locale: comma decimals, '.'/NBSP thousands
separators, values quoted. Descriptions stay in the account language (Portuguese).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import ROOT
from . import analytics, store

REPORTS_DIR = ROOT / "data" / "reports"
ACCOUNT_CSV = "account_report.csv"
POSITION_CSV = "position_report.csv"


def _reports_dir() -> Path:
    with store.connection() as conn:
        meta = store.get_meta(conn, "reports_dir")
    return Path(meta) if meta else REPORTS_DIR


def _to_float(value) -> float | None:
    """Parse a DEGIRO-locale number like '6Â 115,78' or '-4008,50' -> float."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().replace("\xa0", "").replace("Â", "").replace(" ", "")
    if not s:
        return None
    s = s.replace(".", "").replace(",", ".")  # '.' thousands -> remove; ',' decimal -> '.'
    try:
        return float(s)
    except ValueError:
        return None


def position_report_path() -> Path:
    return _reports_dir() / POSITION_CSV


def account_report_path() -> Path:
    return _reports_dir() / ACCOUNT_CSV


def read_position_report() -> dict | None:
    """Official portfolio snapshot -> {securities_value, cash_value, total_value}."""
    path = position_report_path()
    if not path.exists():
        return None
    df = pd.read_csv(path)
    product = df.iloc[:, 0].fillna("").astype(str)
    isin = df.iloc[:, 1].fillna("").astype(str)
    value_eur = df.iloc[:, -1].map(_to_float).fillna(0.0)
    is_cash = product.str.upper().str.startswith("CASH") | (isin.str.strip() == "")
    return {
        "securities_value": float(value_eur[~is_cash].sum()),
        "cash_value": float(value_eur[is_cash].sum()),
        "total_value": float(value_eur.sum()),
    }


def read_position_report_lines() -> pd.DataFrame | None:
    """Official per-holding lines: isin, name, official value (cash row excluded)."""
    path = position_report_path()
    if not path.exists():
        return None
    df = pd.read_csv(path)
    lines = pd.DataFrame({
        "name": df.iloc[:, 0].fillna("").astype(str),
        "isin": df.iloc[:, 1].fillna("").astype(str).str.strip(),
        "official": df.iloc[:, -1].map(_to_float).fillna(0.0),
    })
    return lines[lines["isin"] != ""].reset_index(drop=True)


def read_account_report() -> pd.DataFrame | None:
    """Official account statement with a parsed numeric 'change' column."""
    path = account_report_path()
    if not path.exists():
        return None
    df = pd.read_csv(path)
    out = pd.DataFrame({
        "description": df.iloc[:, 5].fillna("").astype(str),
        "change": df.iloc[:, 8].map(_to_float),
    })
    return out


def _sum_by_keywords(df: pd.DataFrame, keywords: tuple[str, ...]) -> float:
    desc = df["description"].str.lower()
    mask = desc.apply(lambda d: any(k in d for k in keywords))
    return float(df.loc[mask, "change"].dropna().sum())


def crosscheck() -> pd.DataFrame:
    """Compare our reconstructed/computed figures against the official reports."""
    rows = []

    pos = read_position_report()
    if pos is not None:
        daily = analytics.daily_value()
        if not daily.empty:
            last = daily.iloc[-1]
            rows.append(_row("Total portfolio value", float(last["total_value"]),
                             pos["total_value"]))
            rows.append(_row("Securities value", float(last["holdings_value"]),
                             pos["securities_value"]))
            rows.append(_row("Cash", float(last["cash"]), pos["cash_value"]))

    acct = read_account_report()
    if acct is not None:
        div = analytics.dividends()
        app_div = float(div["amount"].sum()) if not div.empty else 0.0
        rows.append(_row("Dividends (total)", app_div,
                         _sum_by_keywords(acct, ("dividend",))))
        fee = analytics.fees()
        app_fee = float(fee["amount"].sum()) if not fee.empty else 0.0
        rows.append(_row("Fees (total)", app_fee,
                         _sum_by_keywords(acct, ("comiss", "taxa", "fee"))))

    return pd.DataFrame(rows)


def crosscheck_holdings() -> pd.DataFrame:
    """Per-holding reconciliation: our latest value vs the position report, by ISIN."""
    official = read_position_report_lines()
    if official is None or official.empty:
        return pd.DataFrame(columns=["holding", "isin", "app", "official", "delta", "match"])

    hist = store.read_df("daily_position_value")
    products = store.read_df("products")[["id", "isin"]]
    app_by_isin: dict[str, float] = {}
    if not hist.empty:
        hist["date"] = pd.to_datetime(hist["date"])
        latest = hist.sort_values("date").groupby("product_id").tail(1)
        merged = latest.merge(products, left_on="product_id", right_on="id", how="left")
        for _, r in merged.iterrows():
            if pd.notna(r.get("isin")):
                app_by_isin[str(r["isin"]).strip()] = float(r["value"])

    rows = []
    for _, line in official.iterrows():
        isin = line["isin"]
        app_value = app_by_isin.get(isin, 0.0)
        delta = app_value - line["official"]
        rows.append({
            "holding": line["name"],
            "isin": isin,
            "app": round(app_value, 2),
            "official": round(float(line["official"]), 2),
            "delta": round(delta, 2),
            "match": "✓" if abs(delta) < 0.5 else "⚠",
        })
    return pd.DataFrame(rows).sort_values("official", ascending=False)


def _row(label: str, app_value: float, official: float) -> dict:
    delta = app_value - official
    return {
        "metric": label,
        "app": round(app_value, 2),
        "official": round(official, 2),
        "delta": round(delta, 2),
        "match": "✓" if abs(delta) < 0.5 else "⚠",
    }
