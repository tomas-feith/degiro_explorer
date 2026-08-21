"""Rebuild a daily portfolio-value time series from transactions + cached prices.

DEGIRO does not expose historical portfolio value, so we:
  1. accumulate signed quantities per product across a daily calendar,
  2. value each holding with its historical close price converted to base currency,
  3. add an approximate cash balance derived from the cash-movement ledger.

The cash figure is an approximation (it sums all cash movements converted to base
currency at the movement date); holdings valuation is exact given good price data.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import numpy as np
import pandas as pd

from . import prices, store

logger = logging.getLogger(__name__)

DEPOSIT_KEYWORDS = ("deposit", "storting", "ideal", "einzahlung", "depósito", "deposito")
WITHDRAWAL_KEYWORDS = ("withdrawal", "terugstorting", "auszahlung", "payout", "levantamento")

# flatexDEGIRO sweeps cash between the DEGIRO account and the flatex bank account.
# These are internal transfers, not real cash flow, so they must be excluded from the
# cash-balance reconstruction (otherwise cash is double-counted against purchases).
INTERNAL_MOVEMENT_TYPES = ("FLATEX_CASH_SWEEP",)


def _calendar(start: date, end: date) -> pd.DatetimeIndex:
    return pd.date_range(start=start, end=end, freq="D")


def _to_naive_day(series: pd.Series) -> pd.Series:
    """Parse DEGIRO timestamps (often timezone-aware) into tz-naive day boundaries.

    The calendar index is tz-naive, so all dates must be made tz-naive to align /
    compare. We convert through UTC and drop the tz, then normalise to midnight.
    """
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    return parsed.dt.tz_localize(None).dt.normalize()


def _fx_frame(calendar: pd.DatetimeIndex, base: str) -> pd.DataFrame:
    """Wide frame: index=calendar, columns=currency, value=units of base per 1 unit."""
    fx = store.read_df("fx_rates")
    frame = pd.DataFrame(index=calendar)
    frame[base.upper()] = 1.0
    if not fx.empty:
        fx["date"] = pd.to_datetime(fx["date"])
        for pair, grp in fx.groupby("pair"):
            cur = pair[: -len(base)]  # pair == CUR+BASE
            s = grp.set_index("date")["rate"].sort_index()
            frame[cur] = s.reindex(calendar).ffill().bfill()
    return frame


def _price_frame(calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """Wide frame: index=calendar, columns=ticker, value=close in product currency."""
    px = store.read_df("prices")
    frame = pd.DataFrame(index=calendar)
    if px.empty:
        return frame
    px["date"] = pd.to_datetime(px["date"])
    for ticker, grp in px.groupby("ticker"):
        s = grp.set_index("date")["close"].sort_index()
        frame[ticker] = s.reindex(calendar).ffill().bfill()
    return frame


def build_daily_value(base_currency: str) -> pd.DataFrame:
    tx = store.read_df("transactions")
    products = store.read_df("products")
    movements = store.read_df("cash_movements")

    if tx.empty:
        logger.warning("No transactions found; nothing to reconstruct.")
        return pd.DataFrame(columns=["date", "holdings_value", "cash", "total_value", "net_invested"])

    tx["date"] = _to_naive_day(tx["date"])
    start = tx["date"].min().date()
    end = date.today()
    calendar = _calendar(start, end)

    mapping, _ = prices.resolve_tickers(products)
    cur_by_pid = products.set_index("id")["currency"].to_dict()

    price_frame = _price_frame(calendar)
    fx_frame = _fx_frame(calendar, base_currency)

    # --- holdings value (aggregate + per product) ---
    holdings = pd.Series(0.0, index=calendar)
    pos_series: dict[int, pd.Series] = {}
    for pid, grp in tx.groupby("product_id"):
        ticker = mapping.get(int(pid))
        if not ticker or ticker not in price_frame:
            continue
        qty = grp.groupby("date")["quantity"].sum().reindex(calendar, fill_value=0.0).cumsum()
        # A London listing is quoted in pence against a GBX product currency: scale the
        # price and convert with GBP, or the row values at zero (no "GBXEUR" pair exists).
        cur, divisor = prices.quote_adjustment(cur_by_pid.get(pid) or base_currency)
        fx = fx_frame[cur] if cur in fx_frame else pd.Series(1.0, index=calendar)
        pos_val = qty * (price_frame[ticker] / divisor) * fx
        pos_series[int(pid)] = pos_val
        holdings = holdings.add(pos_val, fill_value=0.0)

    _save_position_history(pos_series)

    # --- cash balance (approximate) + net invested ---
    cash = pd.Series(0.0, index=calendar)
    net_invested = pd.Series(0.0, index=calendar)
    if not movements.empty:
        movements = movements.copy()
        # Drop internal cash-sweep transfers — they are not real cash flow.
        movements = movements[~movements["type"].isin(INTERNAL_MOVEMENT_TYPES)]
        movements["date"] = _to_naive_day(movements["date"])
        movements["change"] = pd.to_numeric(movements["change"], errors="coerce").fillna(0.0)
        adjusted = movements["currency"].fillna(base_currency).map(prices.quote_adjustment)
        movements["cur"] = [a[0] for a in adjusted]
        movements["divisor"] = [a[1] for a in adjusted]

        def to_base(r):
            fx = fx_frame.get(r["cur"], None)
            rate = fx.asof(r["date"]) if fx is not None else 1.0
            return r["change"] / r["divisor"] * (rate if pd.notna(rate) else 1.0)

        movements["change_base"] = movements.apply(to_base, axis=1)
        daily_change = movements.groupby("date")["change_base"].sum()
        cash = daily_change.reindex(calendar, fill_value=0.0).cumsum()

        desc = movements["description"].fillna("").str.lower()
        is_dep = desc.apply(lambda d: any(k in d for k in DEPOSIT_KEYWORDS))
        is_wd = desc.apply(lambda d: any(k in d for k in WITHDRAWAL_KEYWORDS))
        flows = movements.loc[is_dep | is_wd]
        if not flows.empty:
            net = flows.groupby("date")["change_base"].sum()
            net_invested = net.reindex(calendar, fill_value=0.0).cumsum()

    out = pd.DataFrame(
        {
            "date": calendar.strftime("%Y-%m-%d"),
            "holdings_value": holdings.to_numpy(),
            "cash": cash.to_numpy(),
            "total_value": (holdings + cash).to_numpy(),
            "net_invested": net_invested.to_numpy(),
        }
    )
    out = out.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out = _apply_snapshots(out)  # lock previously-observed days to exact values
    out = _pin_current_day(out, products)  # today = freshest DEGIRO values
    return out


def _apply_snapshots(out: pd.DataFrame) -> pd.DataFrame:
    """Override reconstructed rows with authoritative daily snapshots where present.

    Snapshots were pinned to DEGIRO when captured, so they are exact and must not be
    overwritten by later Yahoo price revisions.

    TODAY IS DELIBERATELY EXCLUDED. Today's row is still moving: a deposit or trade that
    lands after an earlier sync would otherwise be locked out by that sync's snapshot,
    and since every sync re-saves the snapshot from the (already clobbered) frame, the
    stale figure sticks permanently. `_pin_current_day` re-pins holdings and cash from
    DEGIRO afterwards, which masked this for those two columns but not for net_invested
    — a EUR 1,000 same-day deposit went missing from net_invested and inflated P/L by
    exactly that much. Only past days are immutable.
    """
    snaps = store.read_df("value_snapshots")
    if out.empty or snaps.empty:
        return out
    cols = ["holdings_value", "cash", "total_value", "net_invested"]
    snap_by_date = snaps.set_index("date")
    out = out.set_index("date")
    common = out.index.intersection(snap_by_date.index).difference([date.today().isoformat()])
    for c in cols:
        out.loc[common, c] = snap_by_date.loc[common, c]
    return out.reset_index()


def _save_position_history(pos_series: dict[int, pd.Series]) -> None:
    """Persist each holding's daily market value (base currency) for per-holding charts.

    Leading zeros (before the position was opened) are dropped. The latest day is pinned
    to DEGIRO's current position value when available, matching the aggregate pin.
    """
    pin = _current_position_values()
    rows: list[tuple[str, int, float]] = []
    for pid, series in pos_series.items():
        series = series.copy()
        if pid in pin and len(series):
            series.iloc[-1] = pin[pid]
        for ts, value in series.items():
            if value and value > 0:
                rows.append((ts.strftime("%Y-%m-%d"), pid, float(value)))
    with store.connection() as conn:
        store.save_position_values(conn, rows)


def _current_position_values() -> dict[int, float]:
    """product_id -> current value from DEGIRO's portfolio (PRODUCT positions only)."""
    pos = store.read_df("current_positions")
    if pos.empty:
        return {}
    out: dict[int, float] = {}
    for _, row in pos.iterrows():
        ptype = ""
        try:
            raw = json.loads(row["raw"]) if row.get("raw") else {}
            ptype = str(raw.get("positionType", "")).upper()
        except TypeError, ValueError:
            raw = {}
        if ptype and ptype != "PRODUCT":
            continue
        try:
            pid = int(row.get("product_id"))
        except TypeError, ValueError:
            continue
        value = row.get("value")
        if value is not None and not pd.isna(value):
            out[pid] = float(value)
    return out


def _pin_current_day(out: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Override the last day with DEGIRO's exact current portfolio, if available.

    Historical days use Yahoo end-of-day prices; for "today" (the market may be open,
    making Yahoo intraday differ from DEGIRO's figures) we prefer DEGIRO's own values.
    Each position is classified via its ``positionType`` (PRODUCT vs CASH), falling
    back to product-table membership when the type is missing.
    """
    pos = store.read_df("current_positions")
    if out.empty or pos.empty:
        return out

    product_ids = {int(x) for x in products["id"].dropna()}
    holdings_d = cash_d = 0.0
    has_holdings = has_cash = False

    for _, row in pos.iterrows():
        value = row.get("value")
        value = 0.0 if value is None or pd.isna(value) else float(value)
        ptype = ""
        try:
            raw = json.loads(row["raw"]) if row.get("raw") else {}
            ptype = str(raw.get("positionType", "")).upper()
        except TypeError, ValueError:
            pass

        pid = row.get("product_id")
        try:
            pid = int(pid)
        except TypeError, ValueError:
            pid = None

        is_product = ptype == "PRODUCT" or (ptype == "" and pid in product_ids)
        if is_product:
            holdings_d += value
            has_holdings = True
        else:  # CASH or anything not matching a known product
            cash_d += value
            has_cash = True

    last = out.index[-1]
    if has_holdings:
        out.at[last, "holdings_value"] = holdings_d
    if has_cash:
        out.at[last, "cash"] = cash_d
    out.at[last, "total_value"] = out.at[last, "holdings_value"] + out.at[last, "cash"]
    logger.info(
        "Pinned today to DEGIRO: holdings=%.2f cash=%.2f total=%.2f",
        out.at[last, "holdings_value"],
        out.at[last, "cash"],
        out.at[last, "total_value"],
    )
    return out
