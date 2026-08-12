"""Tests for the annualised risk metrics.

The reconstruction calendar is every calendar day (``reconstruct._calendar`` uses
``freq="D"``), so weekend rows carry a forward-filled price and a return of
exactly zero. Annualising that series with 252 mixed a calendar-day sample with
a trading-day constant and understated volatility by ~16% and the annualised
return by ~31%, both of which feed Sharpe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from degiro_explorer import analytics, store


def _seed_daily_value(dates: pd.DatetimeIndex, values: np.ndarray, invested: float) -> None:
    """Write a daily_value series with a single up-front deposit and no later flows."""
    frame = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "holdings_value": values,
            "cash": 0.0,
            "total_value": values,
            "net_invested": invested,
        }
    )
    with store.connection() as conn:
        store.save_daily_value(conn, frame)


@pytest.fixture
def flat_weekday_growth(tmp_db):
    """A portfolio that grows a fixed amount every weekday and is flat at weekends.

    Prices only move on trading days, which is exactly what a forward-filled
    calendar produces.
    """
    dates = pd.date_range("2024-01-01", periods=365 * 2, freq="D")
    daily = 0.0004  # per trading day
    factors = np.where(dates.dayofweek < 5, 1 + daily, 1.0)
    values = 10_000 * np.cumprod(factors)
    _seed_daily_value(dates, values, 10_000.0)
    return dates, daily


def test_metrics_are_measured_on_trading_days_only(flat_weekday_growth) -> None:
    dates, _ = flat_weekday_growth
    metrics = analytics.risk_metrics()

    weekdays = int((dates.dayofweek < 5).sum())
    assert metrics["days"] == len(dates)  # calendar span
    # One observation is consumed by the forced day-0; allow for it landing on
    # either a weekday or a weekend.
    assert metrics["trading_days"] in {weekdays, weekdays - 1}
    assert metrics["trading_days"] < metrics["days"]


def test_annualised_return_matches_the_trading_day_basis(flat_weekday_growth) -> None:
    """A constant per-trading-day return must annualise to itself x 252."""
    _, daily = flat_weekday_growth
    metrics = analytics.risk_metrics()
    expected = daily * analytics.TRADING_DAYS_PER_YEAR * 100
    assert metrics["ann_return_pct"] == pytest.approx(expected, rel=1e-6)


def test_constant_growth_has_no_volatility(flat_weekday_growth) -> None:
    """Weekend zeros used to show up as volatility in a perfectly steady portfolio."""
    metrics = analytics.risk_metrics()
    assert metrics["volatility_pct"] == pytest.approx(0.0, abs=1e-9)


def test_volatility_recovers_a_known_annual_figure(tmp_db) -> None:
    """Simulate a known annualised volatility and check we report it back."""
    rng = np.random.default_rng(11)
    dates = pd.date_range("2015-01-01", periods=365 * 30, freq="D")
    is_weekday = dates.dayofweek < 5
    true_vol = 0.18
    steps = np.ones(len(dates))
    steps[is_weekday] = 1 + rng.normal(0.0, true_vol / np.sqrt(252), int(is_weekday.sum()))
    _seed_daily_value(dates, 10_000 * np.cumprod(steps), 10_000.0)

    reported = analytics.risk_metrics()["volatility_pct"]
    assert reported == pytest.approx(true_vol * 100, rel=0.03)


def test_sharpe_is_undefined_without_volatility(flat_weekday_growth) -> None:
    """A steady portfolio leaves float noise, not an exact 0, so `if vol` let a
    ~1e13 Sharpe through onto the dashboard. It must be nan instead."""
    metrics = analytics.risk_metrics()
    assert metrics["volatility_pct"] == pytest.approx(0.0, abs=1e-9)
    assert np.isnan(metrics["sharpe"])


def test_sharpe_uses_the_configured_risk_free_rate(tmp_db, monkeypatch) -> None:
    from config import settings

    rng = np.random.default_rng(3)
    dates = pd.date_range("2020-01-01", periods=365 * 4, freq="D")
    is_weekday = dates.dayofweek < 5
    steps = np.ones(len(dates))
    steps[is_weekday] = 1 + rng.normal(0.0004, 0.01, int(is_weekday.sum()))
    _seed_daily_value(dates, 10_000 * np.cumprod(steps), 10_000.0)

    monkeypatch.setattr(settings, "risk_free_pct", 0.0)
    zero_rf = analytics.risk_metrics()
    monkeypatch.setattr(settings, "risk_free_pct", 3.0)
    with_rf = analytics.risk_metrics()

    assert zero_rf["risk_free_pct"] == 0.0
    assert with_rf["risk_free_pct"] == 3.0
    # A positive hurdle can only lower the ratio, by exactly rf / vol.
    assert with_rf["sharpe"] < zero_rf["sharpe"]
    assert zero_rf["sharpe"] - with_rf["sharpe"] == pytest.approx(3.0 / zero_rf["volatility_pct"], rel=1e-9)


def test_too_few_rows_returns_empty(tmp_db) -> None:
    dates = pd.date_range("2024-01-01", periods=2, freq="D")
    _seed_daily_value(dates, np.array([100.0, 101.0]), 100.0)
    assert analytics.risk_metrics() == {}


def test_no_data_returns_empty(tmp_db) -> None:
    assert analytics.risk_metrics() == {}
