"""The standalone HTML report: self-contained, correct figures, valid markup."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pandas as pd

from degiro_explorer import report_html, store


def _seed_portfolio() -> None:
    with store.connection() as conn:
        store.save_products(conn, {7: {"isin": "IE00TEST", "symbol": "TST", "name": "Test Fund", "currency": "EUR"}})
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
                }
            ],
        )
        store.save_cash_movements(
            conn,
            [
                {
                    "id": 1,
                    "date": "2026-06-20T09:00:00+02:00",
                    "type": "CASH_TRANSACTION",
                    "description": "Dividendo",
                    "currency": "EUR",
                    "change": 15.0,
                }
            ],
        )
        store.save_current_positions(conn, [{"product_id": 7, "size": 100.0, "price": 12.5, "value": 1250.0}])
        store.save_position_values(conn, [("2026-04-07", 7, 1000.0), ("2026-08-21", 7, 1250.0)])
        store.save_daily_value(
            conn,
            pd.DataFrame(
                [
                    {
                        "date": "2026-04-07",
                        "holdings_value": 1000.0,
                        "cash": 0.0,
                        "total_value": 1000.0,
                        "net_invested": 1000.0,
                    },
                    {
                        "date": "2026-08-21",
                        "holdings_value": 1250.0,
                        "cash": 15.0,
                        "total_value": 1265.0,
                        "net_invested": 1000.0,
                    },
                ]
            ),
        )
        store.set_meta(conn, "base_currency", "EUR")
        store.set_meta(conn, "last_sync", "2026-08-21")


def test_report_is_self_contained(tmp_db):
    """No scripts and no external requests: it must open from a backup folder, offline."""
    _seed_portfolio()
    doc = report_html.build_report()

    assert "<script" not in doc
    # Nothing may reference a URL or a sibling file -- only in-page anchors are allowed.
    assert re.findall(r'(?:src|href)="(?!#)[^"]+"', doc) == []
    assert "http://" not in doc and "https://" not in doc


def test_report_carries_the_headline_figures(tmp_db):
    _seed_portfolio()
    doc = report_html.build_report()

    assert "1,265.00" in doc  # total value
    assert "1,000.00" in doc  # net invested
    assert "265.00" in doc  # total P/L
    assert "Test Fund" in doc
    assert "15.00" in doc  # the dividend
    assert "Portfolio report" in doc


def test_report_charts_are_well_formed_svg(tmp_db):
    _seed_portfolio()
    doc = report_html.build_report()

    svgs = re.findall(r"<svg.*?</svg>", doc, re.S)
    assert svgs, "expected at least one chart"
    for svg in svgs:
        ET.fromstring(svg)  # raises if the hand-written markup is malformed


def test_report_renders_on_an_empty_database(tmp_db):
    """A report before the first sync must still be a valid page, not a traceback."""
    doc = report_html.build_report()
    assert doc.startswith("<!doctype html>")
    assert "Portfolio report" in doc


def test_report_escapes_holding_names(tmp_db):
    """Names come from DEGIRO; they get escaped rather than injected into the markup."""
    _seed_portfolio()
    with store.connection() as conn:
        store.save_products(
            conn, {7: {"isin": "IE00TEST", "symbol": "TST", "name": "<script>x</script>", "currency": "EUR"}}
        )
    doc = report_html.build_report()
    assert "<script>x</script>" not in doc
    assert "&lt;script&gt;" in doc
