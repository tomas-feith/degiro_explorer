"""Resolve products to Yahoo tickers and backfill historical prices + FX rates."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import yaml
import yfinance as yf

from config import settings

from . import store

logger = logging.getLogger(__name__)

# Map DEGIRO exchange ids / common currencies to Yahoo ticker suffixes as a fallback
# heuristic when only a bare symbol is known. Not exhaustive — tickers.yml overrides win.
CURRENCY_SUFFIX = {
    "EUR": "",  # ambiguous; many EUR venues. Left blank — relies on symbol/override.
    "USD": "",
    "GBP": ".L",
    "GBX": ".L",
}


# London quotes in pence while DEGIRO reports the currency as GBX (or GBp). Yahoo
# follows the venue, so a `.L` close is pence: it must be divided by 100 AND converted
# with the GBP rate, not a non-existent "GBXEUR" pair. Without this a GBX holding
# silently values at zero (the FX lookup finds nothing and the product drops out).
def quote_adjustment(currency: str | None) -> tuple[str, float]:
    """(currency to convert with, divisor to apply to the quoted price)."""
    cur = (currency or "").strip()
    if cur.upper() == "GBX" or cur == "GBp":
        return "GBP", 100.0
    return cur.upper(), 1.0


def _load_overrides() -> dict[str, str]:
    path = settings.tickers_file
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = doc.get("overrides") or {}
    # normalise keys to str
    return {str(k): str(v) for k, v in overrides.items()}


def resolve_tickers(products: pd.DataFrame) -> tuple[dict[int, str], list[dict]]:
    """Map product_id -> Yahoo ticker.

    Returns (mapping, unresolved) where unresolved is a list of product dicts that
    need a manual entry in tickers.yml.
    """
    overrides = _load_overrides()
    mapping: dict[int, str] = {}
    unresolved: list[dict] = []

    for _, row in products.iterrows():
        pid = int(row["id"])
        isin = None if pd.isna(row.get("isin")) else row.get("isin")
        symbol = None if pd.isna(row.get("symbol")) else row.get("symbol")

        # 1. explicit override by ISIN or symbol
        ticker = None
        if isin and str(isin) in overrides:
            ticker = overrides[str(isin)]
        elif symbol and str(symbol) in overrides:
            ticker = overrides[str(symbol)]
        # 2. heuristic: use the bare symbol (works for many US/EU listings)
        elif symbol:
            suffix = CURRENCY_SUFFIX.get((row.get("currency") or "").upper(), "")
            ticker = f"{symbol}{suffix}"

        if ticker:
            mapping[pid] = ticker
        else:
            unresolved.append({"id": pid, "isin": isin, "symbol": symbol, "name": row.get("name")})

    return mapping, unresolved


def _download_close(ticker: str, start: date, end: date, auto_adjust: bool = False) -> list[tuple[str, float]]:
    """Daily closes for `ticker`.

    `auto_adjust` back-adjusts history for dividends -- a TOTAL-RETURN series. That is
    right for a benchmark (compared against a portfolio whose dividends are kept) and
    WRONG for valuing holdings: the dividend is already counted as cash, so an adjusted
    price counts it twice, and every new distribution retroactively rewrites the whole
    past series. Measured on IQQY: the stored 2026-04-07 close read 36.045 against a
    real close of 36.745, 1.9% low, and the 2026-08-20 ex-date moved it again.
    """
    try:
        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            progress=False,
            auto_adjust=auto_adjust,
        )
    except Exception:  # noqa: BLE001
        logger.warning("yfinance failed for %s", ticker, exc_info=True)
        return []
    if df is None or df.empty or "Close" not in df:
        return []
    close = df["Close"]
    if isinstance(close, pd.DataFrame):  # multiindex when ticker passed as list
        close = close.iloc[:, 0]
    return [(idx.date().isoformat(), float(v)) for idx, v in close.dropna().items()]


def backfill_prices(mapping: dict[int, str], start: date, end: date) -> dict[str, int]:
    """Download daily closes for every distinct ticker and cache them in SQLite."""
    tickers = sorted(set(mapping.values()))
    counts: dict[str, int] = {}
    with store.connection() as conn:
        for ticker in tickers:
            series = _download_close(ticker, start, end)
            if series:
                store.save_prices(conn, ticker, series)
            counts[ticker] = len(series)
            logger.info("prices %s -> %d rows", ticker, len(series))
    return counts


def load_benchmarks() -> list[str]:
    """Benchmark Yahoo tickers from tickers.yml (default: MSCI World, IWDA.AS)."""
    path = settings.tickers_file
    if not path.exists():
        return ["IWDA.AS"]
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    benchmarks = doc.get("benchmarks")
    return [str(b) for b in benchmarks] if benchmarks else ["IWDA.AS"]


def backfill_benchmarks(start: date, end: date) -> dict[str, int]:
    """Download benchmark daily closes (yfinance) and cache them."""
    counts: dict[str, int] = {}
    with store.connection() as conn:
        for ticker in load_benchmarks():
            # Total return: the benchmark must include its dividends to be comparable.
            series = _download_close(ticker, start, end, auto_adjust=True)
            if series:
                store.save_benchmark_prices(conn, ticker, series)
            counts[ticker] = len(series)
            logger.info("benchmark %s -> %d rows", ticker, len(series))
    return counts


def backfill_fx(currencies: set[str], base: str, start: date, end: date) -> None:
    """Download FX rates converting each foreign currency into the base currency.

    Stored as pair "<CUR><BASE>" with rate = units of base per 1 unit of CUR.
    """
    base = base.upper()
    with store.connection() as conn:
        # GBX is quoted in pence but converted with the GBP rate (see quote_adjustment).
        wanted = {quote_adjustment(c)[0] for c in currencies if c}
        for cur in sorted(wanted):
            if cur == base or not cur:
                continue
            pair = f"{cur}{base}"
            series = _download_close(f"{pair}=X", start, end)
            if series:
                store.save_fx_rates(conn, pair, series)
            logger.info("fx %s -> %d rows", pair, len(series))
