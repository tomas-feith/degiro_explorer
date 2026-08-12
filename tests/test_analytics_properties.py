"""Property-based tests for the money maths.

The bugs found in this module were all edge cases that a hand-written example
happened not to cover: weekend zeros dragging the annualisation, a Sharpe guard
that only caught an exact zero, FIFO silently stopping when the lots ran out.
These assert the invariants instead, so the next one of that family fails here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from degiro_explorer import analytics, store

money = st.floats(min_value=0.01, max_value=1_000_000, allow_nan=False, allow_infinity=False)
rates = st.floats(min_value=0.0, max_value=20.0, allow_nan=False, allow_infinity=False)


# --- Box 3 -----------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(money, rates, money, rates)
def test_box3_tax_is_never_negative(value: float, deemed: float, allowance: float, rate: float):
    """Below the allowance there is no tax; there is never a rebate."""
    out = analytics.box3_tax(value, deemed, allowance, rate)
    assert out["taxable_base"] >= 0.0
    assert out["deemed_income"] >= 0.0
    assert out["tax"] >= 0.0


@settings(max_examples=200, deadline=None)
@given(money, money, rates, rates)
def test_box3_tax_is_monotonic_in_value(a: float, b: float, deemed: float, rate: float):
    """More assets can never mean less tax at the same parameters."""
    low, high = sorted((a, b))
    allowance = 57_000.0
    assert (
        analytics.box3_tax(high, deemed, allowance, rate)["tax"]
        >= analytics.box3_tax(low, deemed, allowance, rate)["tax"] - 1e-9
    )


@settings(max_examples=100, deadline=None)
@given(money, rates, rates)
def test_box3_below_the_allowance_is_untaxed(value: float, deemed: float, rate: float):
    out = analytics.box3_tax(value, deemed, value + 1.0, rate)
    assert out["taxable_base"] == 0.0
    assert out["tax"] == 0.0


# --- TWR -------------------------------------------------------------------


def _daily_frame(values: list[float], invested: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "total_value": np.asarray(values, dtype=float),
            "net_invested": np.asarray(invested, dtype=float),
        }
    )


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.floats(min_value=-0.05, max_value=0.05, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=40,
    ),
    st.floats(min_value=100.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
)
def test_twr_is_unchanged_by_a_mid_series_deposit(returns: list[float], deposit: float):
    """The whole point of time-weighted return: deposits must not move it.

    Same market returns, once with no cash flow and once with a deposit halfway
    through. The compounded TWR has to match -- that is what separates it from
    the value-vs-contributions figure next to it on the dashboard.

    The deposit lands at the END of its day, because that is the convention
    `_twr_factors` implements: `factor = (V_t - flow_t) / V_{t-1}` subtracts the
    whole flow before dividing, so the flow is assumed not to have earned that
    day's return. (A deposit made at the start of a day is therefore credited
    with a day less growth than it really had -- a small, standard
    simplification, not a bug, but it is why this simulation is written this
    way round.)
    """
    start = 10_000.0
    values, invested = [start], [start]
    for r in returns:
        values.append(values[-1] * (1 + r))
        invested.append(start)
    plain = analytics._twr_factors(_daily_frame(values, invested)).cumprod().iloc[-1]

    mid = len(returns) // 2
    v2, inv2 = [start], [start]
    for i, r in enumerate(returns):
        grown = v2[-1] * (1 + r)
        v2.append(grown + (deposit if i == mid else 0.0))
        inv2.append(inv2[-1] + (deposit if i == mid else 0.0))
    with_flow = analytics._twr_factors(_daily_frame(v2, inv2)).cumprod().iloc[-1]

    assert with_flow == pytest.approx(plain, rel=1e-9)


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.floats(min_value=-0.05, max_value=0.05, allow_nan=False, allow_infinity=False),
        min_size=3,
        max_size=30,
    )
)
def test_twr_factors_are_positive_and_start_at_one(returns: list[float]):
    values, invested = [10_000.0], [10_000.0]
    for r in returns:
        values.append(values[-1] * (1 + r))
        invested.append(10_000.0)
    factors = analytics._twr_factors(_daily_frame(values, invested))

    assert factors.iloc[0] == 1.0
    assert (factors > 0).all()


# --- FIFO realised gains ---------------------------------------------------


def _seed_transactions(rows: list[dict]) -> None:
    """Replace the transaction table with exactly `rows`.

    The DELETE matters: hypothesis reuses a function-scoped fixture across all
    examples, and save_transactions upserts by id -- so without it an example
    with two buys would still see the third and fourth from a previous one.
    """
    with store.connection() as conn:
        conn.execute("DELETE FROM transactions")
        store.save_products(conn, {1: {"isin": "T", "symbol": "T", "name": "Test", "currency": "EUR"}})
        store.save_transactions(conn, rows)


def _tx(tid: int, day: str, qty: float, total: float) -> dict:
    return {
        "id": tid,
        "date": f"{day}T10:00:00+00:00",
        "product_id": 1,
        "buysell": "B" if qty > 0 else "S",
        "quantity": qty,
        "price": abs(total / qty) if qty else 0.0,
        "total": total,
        "total_in_base_currency": total,
        "total_plus_all_fees_in_base_currency": total,
        "fee_in_base_currency": 0.0,
    }


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    # Keyword form: with positional strategies hypothesis claims the leading
    # parameters and pytest then tries to resolve them as fixtures.
    buys=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=20),
            st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        ),
        min_size=1,
        max_size=5,
    ),
    sell_qty=st.integers(min_value=1, max_value=20),
)
def test_fifo_never_matches_more_than_was_bought(tmp_db, buys, sell_qty):
    """Matched cost comes from real lots; any shortfall is reported, not hidden.

    The old version stopped silently when the lots ran out, so a sale reaching
    further back than the stored history produced an overstated gain with
    nothing to say so.
    """
    bought = sum(q for q, _ in buys)
    assume(sell_qty <= bought + 10)  # allow both covered and over-sold cases

    rows = [_tx(i, f"2025-01-{i + 1:02d}", float(q), -float(q) * p) for i, (q, p) in enumerate(buys)]
    rows.append(_tx(99, "2025-02-01", -float(sell_qty), float(sell_qty) * 50.0))
    _seed_transactions(rows)

    gains = analytics.realized_gains()
    assert len(gains) == 1
    row = gains.iloc[0]

    assert row["quantity"] == pytest.approx(sell_qty)
    assert row["gain"] == pytest.approx(row["proceeds"] - row["cost"])
    # Unmatched is exactly the shortfall, and zero whenever the lots covered it.
    expected_unmatched = max(0.0, sell_qty - bought)
    assert row["unmatched_quantity"] == pytest.approx(expected_unmatched, abs=1e-6)
    if sell_qty <= bought:
        assert row["unmatched_quantity"] == 0.0


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    qty=st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    price=st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)
def test_selling_at_the_purchase_price_realises_no_gain(tmp_db, qty: float, price: float):
    _seed_transactions(
        [
            _tx(1, "2025-01-01", qty, -qty * price),
            _tx(2, "2025-02-01", -qty, qty * price),
        ]
    )
    row = analytics.realized_gains().iloc[0]
    assert row["gain"] == pytest.approx(0.0, abs=1e-6)
    assert row["unmatched_quantity"] == 0.0
