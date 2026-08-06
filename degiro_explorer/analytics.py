"""Derived metrics for the dashboard: returns, P/L, dividends, allocation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import yaml

from config import ROOT, settings

from . import prices, store


def daily_value() -> pd.DataFrame:
    df = store.read_df("daily_value")
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def summary_kpis() -> dict:
    df = daily_value()
    if df.empty:
        return {}
    latest = df.iloc[-1]
    total_value = float(latest["total_value"])
    net_invested = float(latest["net_invested"])
    pnl = total_value - net_invested
    ret_pct = (pnl / net_invested * 100) if net_invested else 0.0
    return {
        "total_value": total_value,
        "cash": float(latest["cash"]),
        "net_invested": net_invested,
        "total_pnl": pnl,
        "total_return_pct": ret_pct,
    }


def position_value_history() -> pd.DataFrame:
    """Long frame of each holding's market value over time: date, name, value."""
    hist = store.read_df("daily_position_value")
    if hist.empty:
        return pd.DataFrame(columns=["date", "name", "value"])
    hist["date"] = pd.to_datetime(hist["date"])
    products = store.read_df("products")[["id", "name", "symbol"]]
    merged = hist.merge(products, left_on="product_id", right_on="id", how="left")
    merged["name"] = merged["name"].fillna(merged["symbol"]).fillna(merged["product_id"].astype(str))
    return merged[["date", "name", "value"]].sort_values("date")


def position_return_history() -> pd.DataFrame:
    """Long frame of each holding's % return over time: date, name, return_pct.

    return_pct(t) = value(t) / cumulative_cost(t) - 1, where cumulative_cost is the
    base-currency money put into that holding up to date t (handles staggered buys).
    Normalised, so holdings are comparable regardless of position size.
    """
    hist = store.read_df("daily_position_value")
    tx = store.read_df("transactions")
    if hist.empty or tx.empty:
        return pd.DataFrame(columns=["date", "name", "return_pct"])

    hist["date"] = pd.to_datetime(hist["date"])
    tx["date"] = pd.to_datetime(tx["date"], utc=True).dt.tz_localize(None).dt.normalize()
    cost_field = "total_plus_all_fees_in_base_currency"
    tx[cost_field] = pd.to_numeric(tx[cost_field], errors="coerce").fillna(0.0)

    products = store.read_df("products").set_index("id")
    out = []
    for pid, g in hist.groupby("product_id"):
        buys = tx[tx["product_id"] == pid]
        if buys.empty:
            continue
        cum_cost = (-buys.groupby("date")[cost_field].sum()).sort_index().cumsum()
        cost_df = cum_cost.reset_index()
        cost_df.columns = ["date", "cost"]
        merged = pd.merge_asof(
            g[["date", "value"]].sort_values("date"),
            cost_df.sort_values("date"),
            on="date",
        )
        merged = merged[merged["cost"] > 0]
        if merged.empty:
            continue
        merged["return_pct"] = (merged["value"] / merged["cost"] - 1) * 100
        merged["name"] = products.loc[pid, "name"] if pid in products.index else str(pid)
        out.append(merged[["date", "name", "return_pct"]])

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["date", "name", "return_pct"])


def position_performance() -> pd.DataFrame:
    """Per-holding return: current value vs cost basis (both in base currency).

    Currency-safe because it uses DEGIRO's base-currency totals (incl. fees) for cost
    and the reconstructed/pinned current value. Returns one row per current holding.
    """
    hist = store.read_df("daily_position_value")
    tx = store.read_df("transactions")
    if hist.empty or tx.empty:
        return pd.DataFrame(columns=["name", "cost", "value", "pnl", "return_pct"])

    hist["date"] = pd.to_datetime(hist["date"])
    latest = hist.sort_values("date").groupby("product_id").tail(1).set_index("product_id")["value"]

    # Cost basis: net of all buys/sells in base currency (buys negative -> negate).
    cost_field = "total_plus_all_fees_in_base_currency"
    tx[cost_field] = pd.to_numeric(tx[cost_field], errors="coerce").fillna(0.0)
    cost = -tx.groupby("product_id")[cost_field].sum()

    products = store.read_df("products").set_index("id")
    rows = []
    for pid, value in latest.items():
        basis = float(cost.get(pid, 0.0))
        if basis <= 0:
            continue
        name = products.loc[pid, "name"] if pid in products.index else str(pid)
        rows.append(
            {
                "name": name,
                "cost": basis,
                "value": float(value),
                "pnl": float(value) - basis,
                "return_pct": (float(value) / basis - 1) * 100,
            }
        )
    df = pd.DataFrame(rows)
    return df.sort_values("return_pct", ascending=False) if not df.empty else df


def _twr_factors(df: pd.DataFrame) -> pd.Series:
    """Daily time-weighted return factor (1 + daily return), index = df.index.

    factor_t = (V_t - external_flow_t) / V_{t-1}, neutralising deposits/withdrawals.
    """
    value = df["total_value"]
    invested = df["net_invested"]
    flow = invested.diff()
    flow.iloc[0] = invested.iloc[0]
    prev_value = value.shift(1)
    factor = (value - flow) / prev_value
    factor = factor.where(prev_value > 0, 1.0).fillna(1.0)
    factor.iloc[0] = 1.0
    return factor


def performance_curves() -> pd.DataFrame:
    """Deposit-aware performance series over time:

    * pl_vs_invested     = total_value - net_invested (absolute, base currency)
    * pl_vs_invested_pct = (total_value - net_invested) / net_invested * 100
    * twr_pct            = time-weighted return, chaining daily returns while removing
                           the effect of deposit/withdrawal timing and size.
    """
    df = daily_value()
    if df.empty:
        return pd.DataFrame(columns=["date", "pl_vs_invested", "pl_vs_invested_pct", "twr_pct"])

    twr_pct = (_twr_factors(df).cumprod() - 1) * 100
    pl_abs = df["total_value"] - df["net_invested"]
    pl_pct = (pl_abs / df["net_invested"].where(df["net_invested"] > 0)).replace([np.inf, -np.inf], np.nan) * 100

    return pd.DataFrame(
        {
            "date": df["date"],
            "pl_vs_invested": pl_abs.to_numpy(),
            "pl_vs_invested_pct": pl_pct.to_numpy(),
            "twr_pct": twr_pct.to_numpy(),
        }
    )


def benchmark_curves() -> pd.DataFrame:
    """Normalised return (%) of each benchmark over the portfolio's date window.

    Long frame: date, benchmark, return_pct (indexed to 0% at the first shared date).
    """
    daily = daily_value()
    bench = store.read_df("benchmark_prices")
    if daily.empty or bench.empty:
        return pd.DataFrame(columns=["date", "benchmark", "return_pct"])
    start, end = daily["date"].min(), daily["date"].max()
    bench["date"] = pd.to_datetime(bench["date"])
    out = []
    for ticker, grp in bench.groupby("ticker"):
        s = grp.set_index("date")["close"].sort_index()
        s = s[(s.index >= start) & (s.index <= end)]
        if s.empty:
            continue
        base = s.iloc[0]
        if not base:
            continue
        ret = (s / base - 1) * 100
        out.append(pd.DataFrame({"date": ret.index, "benchmark": ticker, "return_pct": ret.to_numpy()}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["date", "benchmark", "return_pct"])


def drawdown_series() -> pd.DataFrame:
    """Deposit-proof drawdown (%) from the TWR index: value vs running peak."""
    df = daily_value()
    if df.empty:
        return pd.DataFrame(columns=["date", "drawdown_pct"])
    index = _twr_factors(df).cumprod()
    drawdown = (index / index.cummax() - 1) * 100
    return pd.DataFrame({"date": df["date"], "drawdown_pct": drawdown.to_numpy()})


def contributions_vs_growth() -> pd.DataFrame:
    """Split total value into contributions (net invested) and market growth."""
    df = daily_value()
    if df.empty:
        return pd.DataFrame(columns=["date", "contributions", "market_growth", "total_value"])
    return pd.DataFrame(
        {
            "date": df["date"],
            "contributions": df["net_invested"],
            "market_growth": df["total_value"] - df["net_invested"],
            "total_value": df["total_value"],
        }
    )


def risk_metrics() -> dict:
    """Portfolio risk from deposit-proof daily TWR returns (annualised)."""
    df = daily_value()
    if df.empty or len(df) < 3:
        return {}
    daily_ret = _twr_factors(df) - 1
    daily_ret = daily_ret.iloc[1:]  # drop the forced day-0 = 0
    vol = float(daily_ret.std() * np.sqrt(252) * 100)
    mean_ann = float(daily_ret.mean() * 252 * 100)
    # Sharpe is the EXCESS return per unit of volatility. The risk-free rate is
    # configurable (DEGIRO_RISK_FREE_PCT); with EUR short rates well above zero,
    # leaving it at 0 overstates the ratio.
    rf = float(settings.risk_free_pct)
    sharpe = ((mean_ann - rf) / vol) if vol else 0.0
    max_dd = float(drawdown_series()["drawdown_pct"].min())
    return {
        "volatility_pct": vol,
        "ann_return_pct": mean_ann,
        "sharpe": sharpe,
        "risk_free_pct": rf,
        "max_drawdown_pct": max_dd,
        "days": int(len(df)),
    }


def current_holdings() -> pd.DataFrame:
    """Current security positions joined with product metadata (cash row excluded)."""
    pos = store.read_df("current_positions")
    products = store.read_df("products")[["id", "isin", "symbol", "name", "currency"]]
    if pos.empty:
        return pd.DataFrame(columns=["name", "symbol", "isin", "currency", "size", "price", "value"])
    # product_id is TEXT (cash uses string ids); coerce for the numeric join, drop cash.
    pos = pos.copy()
    pos["pid"] = pd.to_numeric(pos["product_id"], errors="coerce")
    pos = pos.dropna(subset=["pid"])
    merged = pos.merge(products, left_on="pid", right_on="id", how="left")
    cols = ["name", "symbol", "isin", "currency", "size", "price", "value"]
    return merged[[c for c in cols if c in merged]].sort_values("value", ascending=False)


def _movements_by_keyword(keywords: tuple[str, ...]) -> pd.DataFrame:
    mv = store.read_df("cash_movements")
    if mv.empty:
        return mv
    mv["date"] = pd.to_datetime(mv["date"], utc=True).dt.tz_localize(None)
    mv["change"] = pd.to_numeric(mv["change"], errors="coerce").fillna(0.0)
    desc = mv["description"].fillna("").str.lower()
    mask = desc.apply(lambda d: any(k in d for k in keywords))
    return mv.loc[mask]


def dividends() -> pd.DataFrame:
    div = _movements_by_keyword(("dividend",))
    if div.empty:
        return pd.DataFrame(columns=["month", "amount", "currency"])
    div = div.assign(month=div["date"].dt.to_period("M").astype(str))
    return div.groupby(["month", "currency"], as_index=False)["change"].sum().rename(columns={"change": "amount"})


def fees() -> pd.DataFrame:
    fee = _movements_by_keyword(("fee", "commission", "cost", "kosten", "comiss", "taxa"))
    if fee.empty:
        return pd.DataFrame(columns=["month", "amount", "currency"])
    fee = fee.assign(month=fee["date"].dt.to_period("M").astype(str))
    return fee.groupby(["month", "currency"], as_index=False)["change"].sum().rename(columns={"change": "amount"})


def dividend_yield() -> pd.DataFrame:
    """Trailing dividend yield per holding: total dividends received / current value."""
    mv = store.read_df("cash_movements")
    sec = current_securities()
    if mv.empty or sec.empty:
        return pd.DataFrame(columns=["name", "dividends", "value", "yield_pct"])
    mv = mv.copy()
    mv["change"] = pd.to_numeric(mv["change"], errors="coerce").fillna(0.0)
    is_div = mv["description"].fillna("").str.lower().str.contains("dividend")
    div_by_pid = mv.loc[is_div].groupby("product_id")["change"].sum()

    products = store.read_df("products")[["id", "isin"]]
    isin_by_id = products.set_index("id")["isin"].to_dict()
    div_by_isin: dict[str, float] = {}
    for pid, amt in div_by_pid.items():
        isin = isin_by_id.get(pid)
        if isin:
            div_by_isin[str(isin)] = div_by_isin.get(str(isin), 0.0) + float(amt)

    sec = sec.copy()
    sec["dividends"] = sec["isin"].map(lambda i: div_by_isin.get(str(i), 0.0))
    sec["yield_pct"] = (sec["dividends"] / sec["value"].where(sec["value"] > 0)) * 100
    return sec[["name", "dividends", "value", "yield_pct"]].sort_values("yield_pct", ascending=False)


def upcoming_payments() -> pd.DataFrame:
    df = store.read_df("upcoming_payments")
    if df.empty:
        return pd.DataFrame(columns=["pay_date", "product", "currency", "amount", "description"])
    return df[["pay_date", "product", "currency", "amount", "description"]].sort_values("pay_date")


def transactions() -> pd.DataFrame:
    tx = store.read_df("transactions")
    products = store.read_df("products")[["id", "name", "symbol"]]
    if tx.empty:
        return tx
    tx["date"] = pd.to_datetime(tx["date"])
    merged = tx.merge(products, left_on="product_id", right_on="id", how="left")
    cols = ["date", "name", "symbol", "buysell", "quantity", "price", "total_plus_all_fees_in_base_currency"]
    return merged[[c for c in cols if c in merged]].sort_values("date", ascending=False)


def _holdings_meta() -> dict:
    path = ROOT / "holdings_meta.yml"
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): (v or {}) for k, v in (doc.get("meta") or {}).items()}


def current_securities() -> pd.DataFrame:
    """Current security positions with isin, name, value, weight% (cash excluded)."""
    pos = store.read_df("current_positions")
    products = store.read_df("products")[["id", "isin", "name"]]
    if pos.empty:
        return pd.DataFrame(columns=["isin", "name", "value", "weight"])
    pos = pos.copy()
    pos["pid"] = pd.to_numeric(pos["product_id"], errors="coerce")
    pos = pos.dropna(subset=["pid"])
    pos["value"] = pd.to_numeric(pos["value"], errors="coerce").fillna(0.0)
    merged = pos.merge(products, left_on="pid", right_on="id", how="left")
    total = merged["value"].sum()
    merged["weight"] = merged["value"] / total * 100 if total else 0.0
    return merged[["isin", "name", "value", "weight"]].sort_values("value", ascending=False)


def holdings_classification() -> pd.DataFrame:
    """Current holdings enriched with category / region / theme / TER from metadata."""
    sec = current_securities()
    if sec.empty:
        return sec
    meta = _holdings_meta()
    sec = sec.copy()
    sec["category"] = sec["isin"].map(lambda i: meta.get(i, {}).get("category", "unclassified"))
    sec["region"] = sec["isin"].map(lambda i: meta.get(i, {}).get("region", "unknown"))
    sec["theme"] = sec["isin"].map(lambda i: meta.get(i, {}).get("theme", "unknown"))
    sec["ter"] = sec["isin"].map(lambda i: meta.get(i, {}).get("ter"))
    return sec


def ter_summary() -> dict:
    """Weighted average TER and estimated annual cost (over holdings with a known TER)."""
    df = holdings_classification()
    if df.empty:
        return {}
    known = df.dropna(subset=["ter"])
    if known.empty or known["value"].sum() == 0:
        return {}
    weighted = float((known["value"] * known["ter"]).sum() / known["value"].sum())
    annual_cost = float((known["value"] * known["ter"] / 100).sum())
    coverage = float(known["value"].sum() / df["value"].sum() * 100)
    return {"weighted_ter": weighted, "annual_cost": annual_cost, "coverage_pct": coverage}


def returns_correlation() -> pd.DataFrame:
    """Correlation matrix of daily returns across current holdings (by name).

    Uses the cached daily price series; restricted to currently-resolved tickers so
    stale/incorrect tickers don't pollute the matrix.
    """
    px = store.read_df("prices")
    if px.empty:
        return pd.DataFrame()
    products = store.read_df("products")
    mapping, _ = prices.resolve_tickers(products)  # product_id -> ticker
    name_by_id = products.set_index("id")["name"].to_dict()
    valid = {t: name_by_id.get(pid, t) for pid, t in mapping.items()}

    px = px[px["ticker"].isin(valid)].copy()
    if px.empty:
        return pd.DataFrame()
    px["date"] = pd.to_datetime(px["date"])
    wide = px.pivot_table(index="date", columns="ticker", values="close").sort_index()
    rets = wide.pct_change().dropna(how="all")
    corr = rets.corr()
    return corr.rename(index=valid, columns=valid)


def box3_reference_values() -> pd.DataFrame:
    """Portfolio value on 1 January of each year (the NL Box 3 'peildatum').

    Only years whose 1-Jan falls within the reconstructed series are returned. For an
    account opened mid-year, the first relevant peildatum is the following 1 January.
    """
    df = daily_value()
    if df.empty:
        return pd.DataFrame(columns=["year", "reference_date", "value"])
    indexed = df.set_index("date")["total_value"]
    rows = []
    for year in range(indexed.index.min().year, indexed.index.max().year + 1):
        jan1 = pd.Timestamp(year=year, month=1, day=1)
        if jan1 in indexed.index:
            rows.append({"year": year, "reference_date": jan1.date().isoformat(), "value": float(indexed.loc[jan1])})
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class Box3Params:
    """NL Box 3 parameters for one tax year (investments / 'overige bezittingen')."""

    deemed_return_pct: float
    allowance: float  # heffingsvrij vermogen, per person
    rate_pct: float
    provisional: bool = False


# Verify against the Belastingdienst each year — Box 3 is mid-reform toward actual
# return, and announced figures do get revised before they are enacted (the planned
# 2026 hike to 7.78% with a EUR 51,396 allowance was scrapped; the enacted figures
# are 6.00% and EUR 59,357).
BOX3_PARAMS: dict[int, Box3Params] = {
    2024: Box3Params(deemed_return_pct=6.04, allowance=57000.0, rate_pct=36.0),
    2025: Box3Params(deemed_return_pct=5.88, allowance=57684.0, rate_pct=36.0),
    2026: Box3Params(deemed_return_pct=6.00, allowance=59357.0, rate_pct=36.0),
}

LATEST_BOX3_YEAR = max(BOX3_PARAMS)


def box3_params(year: int) -> Box3Params:
    """Parameters for `year`, falling back to the most recent year we know about."""
    return BOX3_PARAMS.get(year, BOX3_PARAMS[LATEST_BOX3_YEAR])


def box3_tax(value: float, deemed_return_pct: float, allowance: float, rate_pct: float) -> dict:
    """Rough NL Box 3 tax on an investments-only base. Indicative only, not advice.

    Valid simplification for a single asset class (investments, no savings/debts):
    tax = max(0, value - allowance) * deemed_return% * rate%.
    """
    taxable_base = max(0.0, value - allowance)
    deemed_income = taxable_base * deemed_return_pct / 100.0
    tax = deemed_income * rate_pct / 100.0
    return {"taxable_base": taxable_base, "deemed_income": deemed_income, "tax": tax}


def realized_gains() -> pd.DataFrame:
    """FIFO-matched realized gains/losses per disposal (base currency).

    Empty until you sell. In NL Box 3 these are NOT taxed for private investors — this
    report is informational (and useful if your situation ever differs).
    """
    tx = store.read_df("transactions")
    if tx.empty or (tx["quantity"] < 0).sum() == 0:
        return pd.DataFrame(columns=["date", "name", "quantity", "proceeds", "cost", "gain"])

    tx = tx.copy()
    tx["date"] = pd.to_datetime(tx["date"], utc=True).dt.tz_localize(None)
    cost_field = "total_plus_all_fees_in_base_currency"
    tx[cost_field] = pd.to_numeric(tx[cost_field], errors="coerce").fillna(0.0)
    products = store.read_df("products").set_index("id")["name"].to_dict()

    rows = []
    for pid, grp in tx.sort_values("date").groupby("product_id"):
        lots: list[list[float]] = []  # [qty_remaining, unit_cost_base]
        for _, t in grp.iterrows():
            qty = float(t["quantity"])
            unit = abs(float(t[cost_field])) / abs(qty) if qty else 0.0
            if qty > 0:
                lots.append([qty, unit])
            elif qty < 0:
                sell_qty = -qty
                proceeds = abs(float(t[cost_field]))
                unit_proceeds = proceeds / sell_qty if sell_qty else 0.0
                matched_cost = 0.0
                remaining = sell_qty
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    take = min(lot[0], remaining)
                    matched_cost += take * lot[1]
                    lot[0] -= take
                    remaining -= take
                    if lot[0] <= 1e-9:
                        lots.pop(0)
                rows.append(
                    {
                        "date": t["date"].date().isoformat(),
                        "name": products.get(pid, str(pid)),
                        "quantity": sell_qty,
                        "proceeds": unit_proceeds * sell_qty,
                        "cost": matched_cost,
                        "gain": unit_proceeds * sell_qty - matched_cost,
                    }
                )
    return pd.DataFrame(rows)


def reconstruction_delta(reconstructed_holdings: float) -> dict:
    """Compare reconstructed holdings to DEGIRO's live security positions (cash excluded)."""
    pos = store.read_df("current_positions")
    if pos.empty:
        actual = 0.0
    else:
        # Securities only — cash positions use non-numeric ids (e.g. 'FLATEX_EUR').
        is_security = pd.to_numeric(pos["product_id"], errors="coerce").notna()
        actual = float(pd.to_numeric(pos.loc[is_security, "value"], errors="coerce").sum())
    delta = reconstructed_holdings - actual
    pct = (delta / actual * 100) if actual else 0.0
    return {"reconstructed": reconstructed_holdings, "actual_holdings": actual, "delta": delta, "delta_pct": pct}
