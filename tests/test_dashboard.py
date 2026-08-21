"""Smoke test: the Streamlit app renders end-to-end without raising.

dashboard/ is excluded from coverage and has no unit tests -- it is "exercised by
running it", which in practice meant nobody noticed until a page was opened. Streamlit's
AppTest runs the real script in-process, so a broken column_config, a renamed analytics
key or a deprecated widget argument fails here instead of in the browser. It caught the
`use_container_width` removal (deprecated after 2025-12-31, still in every chart call).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from degiro_explorer import store

pytest.importorskip("streamlit.testing.v1")

# Absolute, NOT "dashboard/app.py": AppTest.from_file resolves a relative path against
# the file that calls it (so pytest would look for tests/dashboard/app.py). Streamlit
# 1.60 happened to resolve it against the cwd instead, which hid this locally and only
# failed in CI on the 1.61 bump.
APP = str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py")


def _seed() -> None:
    """One holding, one dividend, a buy and a partial sale -- enough for every tab."""
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
                },
                {
                    "id": 2,
                    "date": "2026-06-10T08:00:00+02:00",
                    "product_id": 7,
                    "buysell": "S",
                    "quantity": -40.0,
                    "price": 12.0,
                    "total": 480.0,
                    "total_in_base_currency": 480.0,
                    "total_plus_all_fees_in_base_currency": 480.0,
                    "fee_in_base_currency": 0.0,
                },
            ],
        )
        store.save_cash_movements(
            conn,
            [
                {
                    "id": 1,
                    "date": "2026-04-01T09:00:00+02:00",
                    "type": "CASH_TRANSACTION",
                    "description": "Depositos",
                    "currency": "EUR",
                    "change": 1000.0,
                },
                {
                    "id": 2,
                    "date": "2026-06-20T09:00:00+02:00",
                    "type": "CASH_TRANSACTION",
                    "description": "Dividendo",
                    "currency": "EUR",
                    "change": 15.0,
                },
            ],
        )
        store.save_current_positions(conn, [{"product_id": 7, "size": 60.0, "price": 12.5, "value": 750.0}])
        store.save_prices(conn, "TST", [("2026-04-07", 10.05), ("2026-06-10", 12.02)])
        store.save_position_values(conn, [("2026-04-07", 7, 1005.0), ("2026-08-21", 7, 750.0)])
        store.save_daily_value(
            conn,
            pd.DataFrame(
                [
                    {
                        "date": "2026-04-07",
                        "holdings_value": 1005.0,
                        "cash": 0.0,
                        "total_value": 1005.0,
                        "net_invested": 1000.0,
                    },
                    {
                        "date": "2026-08-21",
                        "holdings_value": 750.0,
                        "cash": 495.0,
                        "total_value": 1245.0,
                        "net_invested": 1000.0,
                    },
                ]
            ),
        )
        store.set_meta(conn, "base_currency", "EUR")
        store.set_meta(conn, "last_sync", "2026-08-21")


def _run():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(APP, default_timeout=120)
    app.run()
    return app


def test_dashboard_renders_with_data(tmp_db):
    _seed()
    app = _run()
    assert not app.exception, [e.value for e in app.exception]
    assert not app.error, [e.value for e in app.error]


def test_dashboard_renders_on_an_empty_database(tmp_db):
    """A fresh DB must show the "run sync first" path, not a traceback."""
    app = _run()
    assert not app.exception, [e.value for e in app.exception]


def test_overview_report_button_produces_a_downloadable_document(tmp_db):
    """The button builds the report and hands it to a download_button."""
    _seed()
    app = _run()
    assert not app.exception, [e.value for e in app.exception]

    buttons = [b for b in app.button if "HTML report" in b.label]
    assert buttons, [b.label for b in app.button]
    buttons[0].click().run()
    assert not app.exception, [e.value for e in app.exception]

    downloads = [d for d in app.get("download_button") if "HTML report" in d.label]
    assert downloads, "no download button appeared after preparing the report"
    assert "Portfolio report" in app.session_state["report_html"]
