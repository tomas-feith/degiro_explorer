"""Download raw data from DEGIRO and normalise it into plain dicts."""
from __future__ import annotations

import logging
from datetime import date

from degiro_connector.trading.models.account import (
    Format,
    OverviewRequest,
    ReportRequest,
    UpdateOption,
    UpdateRequest,
)
from degiro_connector.trading.models.transaction import HistoryRequest

from .client import Session

logger = logging.getLogger(__name__)

# DEGIRO was founded in 2013; safe lower bound when the open date is unknown.
DEFAULT_START_YEAR = 2013


def _year_ranges(start_year: int, end: date) -> list[tuple[date, date]]:
    ranges = []
    for year in range(start_year, end.year + 1):
        frm = date(year, 1, 1)
        to = date(year, 12, 31) if year < end.year else end
        ranges.append((frm, to))
    return ranges


def fetch_transactions(session: Session, start_year: int = DEFAULT_START_YEAR) -> list[dict]:
    """All trades, fetched year-by-year (DEGIRO rejects very wide ranges)."""
    api = session.api
    today = date.today()
    out: list[dict] = []
    for frm, to in _year_ranges(start_year, today):
        result = api.get_transactions_history(
            transaction_request=HistoryRequest(from_date=frm, to_date=to),
            raw=False,
        )
        items = getattr(result, "data", None) or []
        logger.info("transactions %s..%s -> %d", frm, to, len(items))
        for item in items:
            out.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))
    return out


def fetch_cash_movements(session: Session, start_year: int = DEFAULT_START_YEAR) -> list[dict]:
    """Cash ledger: deposits, withdrawals, dividends, fees, interest, FX."""
    api = session.api
    today = date.today()
    out: list[dict] = []
    for frm, to in _year_ranges(start_year, today):
        result = api.get_account_overview(
            overview_request=OverviewRequest(from_date=frm, to_date=to),
            raw=False,
        )
        movements = getattr(result, "cash_movements", None) or []
        logger.info("cash movements %s..%s -> %d", frm, to, len(movements))
        for mv in movements:
            out.append(mv.model_dump() if hasattr(mv, "model_dump") else dict(mv))
    return out


def fetch_products(session: Session, product_ids: list[int]) -> dict[int, dict]:
    """Product metadata (isin, symbol, name, currency, ...) for the given ids.

    Cash positions use non-numeric ids (e.g. 'FLATEX_EUR'); those are skipped here.
    """
    api = session.api
    ids = sorted({i for i in (_as_int(pid) for pid in product_ids) if i is not None})
    products: dict[int, dict] = {}
    # Chunk to keep request sizes reasonable.
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        result = api.get_products_info(product_list=chunk, raw=False)
        data = getattr(result, "data", None) or {}
        for key, item in data.items():
            rec = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            products[int(key)] = rec
    logger.info("fetched %d/%d products", len(products), len(ids))
    return products


def fetch_current_portfolio(session: Session) -> list[dict]:
    """Current holdings, used to validate the reconstruction (today's real numbers)."""
    api = session.api
    update = api.get_update(
        request_list=[UpdateRequest(option=UpdateOption.PORTFOLIO, last_updated=0)],
        raw=True,
    )
    portfolio = (update or {}).get("portfolio", {})
    rows = portfolio.get("value", []) if isinstance(portfolio, dict) else []
    positions: list[dict] = []
    for row in rows:
        flat = {"product_id": _to_int(row.get("id"))}
        for attr in row.get("value", []):
            flat[attr.get("name")] = attr.get("value")
        positions.append(flat)
    logger.info("current portfolio positions: %d", len(positions))
    return positions


def fetch_upcoming_payments(session: Session) -> list[dict]:
    """Expected upcoming dividend/coupon payments (best-effort; may be empty)."""
    try:
        result = session.api.get_upcoming_payments(raw=True)
    except Exception:  # noqa: BLE001
        logger.warning("Could not fetch upcoming payments", exc_info=True)
        return []
    data = result.get("data", result) if isinstance(result, dict) else []
    if not isinstance(data, list):
        data = []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append({
            "product_id": item.get("caId") or item.get("productId"),
            "product": item.get("product"),
            "currency": item.get("currency"),
            "amount": item.get("amount"),
            "pay_date": item.get("payDate"),
            "description": item.get("description"),
        })
    logger.info("upcoming payments: %d", len(out))
    return out


def fetch_account_report(session: Session, frm: date, to: date,
                         country: str = "NL", lang: str = "en") -> str | None:
    """Official DEGIRO account statement (cash movements) as CSV text."""
    req = ReportRequest(country=country, lang=lang, format=Format.CSV,
                        from_date=frm, to_date=to, int_account=session.int_account)
    rep = session.api.get_account_report(report_request=req, raw=False)
    content = getattr(rep, "content", None) if rep is not None else None
    logger.info("account report %s..%s -> %s chars", frm, to, len(content) if content else 0)
    return content


def fetch_position_report(session: Session, on: date,
                          country: str = "NL", lang: str = "en") -> str | None:
    """Official DEGIRO portfolio snapshot (positions + values) as CSV text."""
    req = ReportRequest(country=country, lang=lang, format=Format.CSV,
                        from_date=on, to_date=on, int_account=session.int_account)
    rep = session.api.get_position_report(report_request=req, raw=False)
    content = getattr(rep, "content", None) if rep is not None else None
    logger.info("position report %s -> %s chars", on, len(content) if content else 0)
    return content


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _as_int(value):
    """int(value) or None for non-numeric ids (e.g. cash 'FLATEX_EUR')."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
