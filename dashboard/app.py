"""Streamlit dashboard for exploring reconstructed DEGIRO history.

Reads only the local SQLite database produced by scripts/sync.py — no live DEGIRO calls.
Run with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from degiro_explorer import analytics, reports, store  # noqa: E402

st.set_page_config(page_title="DEGIRO Explorer", page_icon="📈", layout="wide")


@st.cache_data(ttl=300)
def _load():
    # One render fans out over ~26 analytics functions that each re-read whole tables
    # through their own connection (55 reads before this). The block is read-only.
    with store.cached_reads():
        return _derive()


def _derive():
    transaction_pnl = analytics.transaction_pnl()
    return {
        "daily": analytics.daily_value(),
        "kpis": analytics.summary_kpis(),
        "holdings": analytics.current_holdings(),
        "dividends": analytics.dividends(),
        "fees": analytics.fees(),
        "transactions": analytics.transactions(),
        "position_history": analytics.position_value_history(),
        "position_return_history": analytics.position_return_history(),
        "position_performance": analytics.position_performance(),
        "performance_curves": analytics.performance_curves(),
        "box3_reference": analytics.box3_reference_values(),
        "realized_gains": analytics.realized_gains(),
        "transaction_pnl": transaction_pnl,
        # Pass the ledger in: it is the same FIFO walk, and it is the costliest one.
        "pnl_reconciliation": analytics.pnl_reconciliation(transaction_pnl),
        "lot_matches": analytics.lot_matches(),
        "crosscheck": reports.crosscheck(),
        "crosscheck_holdings": reports.crosscheck_holdings(),
        "classification": analytics.holdings_classification(),
        "ter_summary": analytics.ter_summary(),
        "correlation": analytics.returns_correlation(),
        "benchmark_curves": analytics.benchmark_curves(),
        "drawdown": analytics.drawdown_series(),
        "contributions": analytics.contributions_vs_growth(),
        "risk_metrics": analytics.risk_metrics(),
        "dividend_yield": analytics.dividend_yield(),
        "upcoming_payments": analytics.upcoming_payments(),
        "ticker_check": analytics.ticker_price_check(),
        "price_freshness": analytics.price_freshness(),
    }


def main() -> None:
    st.title("📈 DEGIRO Explorer")

    if not settings.db_file.exists():
        st.warning("No database yet. Run `python scripts/sync.py` first.")
        return

    try:
        with store.connection() as conn:
            base = store.get_meta(conn, "base_currency", "EUR")
            last_sync = store.get_meta(conn, "last_sync", "never")
    except Exception as exc:
        # The file exists but is not usable: a schema older than the current
        # migrations, a half-written DB from an interrupted sync, or another
        # process holding it locked.
        st.error(f"**Could not read the database.**\n\n`{type(exc).__name__}: {exc}`")
        st.info(
            f"`{settings.db_file}` exists but could not be opened. If a sync was "
            "interrupted, re-run `python scripts/sync.py --offline` to rebuild the "
            "derived tables; if the schema predates the current migrations, delete "
            "the file and run a full sync."
        )
        return
    st.caption(f"Base currency: **{base}** · Last sync: **{last_sync}**")

    with st.expander("📖 Glossary — what the abbreviations mean"):
        st.markdown(
            "- **ETF** — Exchange-Traded Fund: a fund tracking an index/theme, traded like a share.\n"
            "- **ISIN** — International Securities Identification Number: a security's unique 12-character code.\n"
            "- **P/L** — Profit / Loss: current value minus the money you put in.\n"
            "- **TWR** — Time-Weighted Return: performance that removes the timing/size of your "
            "deposits (the deposit-proof, broker-standard measure).\n"
            "- **TER** — Total Expense Ratio: a fund's annual running cost, as a % of assets.\n"
            "- **FIFO** — First-In, First-Out: oldest shares are treated as sold first when "
            "matching buys to sells for realised gains.\n"
            "- **YTD** — Year-To-Date: from 1 January to now. **1M/3M/1Y** = trailing 1/3 months, 1 year.\n"
            "- **FX** — Foreign Exchange (currency conversion rate).\n"
            "- **Box 3** — the Dutch wealth-tax category on savings & investments.\n"
            "- **Peildatum** — the Box 3 reference date (1 January) on which your asset value is measured."
        )

    try:
        data = _load()
    except Exception as exc:
        # _load fans out over every analytics function; one failing table should
        # name itself rather than dropping a traceback into the middle of the page.
        st.error(f"**Could not derive the dashboard data.**\n\n`{type(exc).__name__}: {exc}`")
        st.info(
            "Re-run `python scripts/sync.py --offline` to rebuild the derived tables "
            "from stored data (no DEGIRO login needed). If that fails too, the raw "
            "tables are the problem and a full sync is required."
        )
        return
    if data["daily"].empty:
        st.error("No reconstructed data. Check the sync logs for unresolved tickers.")
        return

    tabs = st.tabs(["Overview", "Performance", "Holdings", "Transactions & Income", "Tax (NL Box 3)", "Data"])

    # --- Overview ---
    with tabs[0]:
        k = data["kpis"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total value", f"{k.get('total_value', 0):,.0f} {base}")
        c2.metric("Net invested", f"{k.get('net_invested', 0):,.0f} {base}")
        c3.metric(
            "Total P/L",
            f"{k.get('total_pnl', 0):,.0f} {base}",
            help="Profit / Loss — current value minus money invested.",
        )
        c4.metric("Total return", f"{k.get('total_return_pct', 0):,.1f}%")

        df = data["daily"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["date"], y=df["total_value"], name="Total value", fill="tozeroy"))
        fig.add_trace(go.Scatter(x=df["date"], y=df["net_invested"], name="Net invested", line={"dash": "dash"}))
        fig.update_layout(title="Portfolio value over time", hovermode="x unified", height=460)
        st.plotly_chart(fig, width="stretch")

        st.subheader("Contributions vs market growth")
        cg = data["contributions"]
        cgfig = go.Figure()
        cgfig.add_trace(
            go.Scatter(x=cg["date"], y=cg["contributions"], name="Contributions", stackgroup="one", line={"width": 0.5})
        )
        cgfig.add_trace(
            go.Scatter(x=cg["date"], y=cg["market_growth"], name="Market growth", stackgroup="one", line={"width": 0.5})
        )
        cgfig.update_layout(height=320, hovermode="x unified")
        st.plotly_chart(cgfig, width="stretch")
        st.caption(
            "How much of your total value is money you added vs. market gains. "
            "(If markets are down, 'market growth' can be negative.)"
        )

    # --- Performance ---
    with tabs[1]:
        curves = data["performance_curves"]
        if curves.empty:
            st.info("No performance data yet — run sync.")
        else:
            st.caption(
                "**TWR (Time-Weighted Return)** neutralises the timing/size of your "
                "deposits — the standard, deposit-proof performance measure. "
                "**P/L (Profit/Loss) vs invested** is simply how far your total value sits "
                "above the money you've put in."
            )
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=curves["date"], y=curves["twr_pct"], name="TWR (Time-Weighted Return)"))
            fig.add_trace(
                go.Scatter(
                    x=curves["date"], y=curves["pl_vs_invested_pct"], name="P/L vs invested", line={"dash": "dash"}
                )
            )
            # Benchmark overlay(s)
            bench = data["benchmark_curves"]
            for name, grp in bench.groupby("benchmark"):
                fig.add_trace(
                    go.Scatter(
                        x=grp["date"],
                        y=grp["return_pct"],
                        name=f"Benchmark: {name}",
                        line={"dash": "dot", "color": "gray"},
                    )
                )
            fig.update_layout(
                title="Return over time (%) — vs benchmark", hovermode="x unified", height=440, yaxis_ticksuffix="%"
            )
            st.plotly_chart(fig, width="stretch")
            if not bench.empty:
                st.caption(
                    "Benchmark = a buy-and-hold index for comparison (set in "
                    "tickers.yml). If it's above your TWR line, the index outperformed."
                )

            # Absolute P/L (base currency) over time
            if "pl_vs_invested" in curves:
                plfig = go.Figure()
                plfig.add_trace(
                    go.Scatter(
                        x=curves["date"],
                        y=curves["pl_vs_invested"],
                        name="P/L vs invested",
                        fill="tozeroy",
                        line={"color": "#2e9e5b"},
                    )
                )
                plfig.update_layout(
                    title=f"P/L over time ({base}) — value minus money invested",
                    hovermode="x unified",
                    height=320,
                    yaxis_tickprefix=f"{base} ",
                )
                plfig.add_hline(y=0, line_dash="dot", line_color="gray")
                st.plotly_chart(plfig, width="stretch")
                st.caption(
                    "Absolute profit/loss in cash terms: how many "
                    f"{base} your portfolio is above (or below) the money "
                    "you've contributed, on each day."
                )

        # Drawdown
        dd = data["drawdown"]
        if not dd.empty:
            st.subheader("Drawdown")
            ddfig = px.area(dd, x="date", y="drawdown_pct", height=280, labels={"drawdown_pct": "Drawdown %"})
            ddfig.update_traces(line_color="#d6455d", fillcolor="rgba(214,69,93,0.2)")
            ddfig.update_layout(yaxis_ticksuffix="%")
            st.plotly_chart(ddfig, width="stretch")
            st.caption(
                "Decline from the portfolio's running peak (deposit-proof, from the "
                "TWR index) — shows the worst dips you've sat through."
            )

        # Risk metrics
        risk = data["risk_metrics"]
        if risk:
            st.subheader("Risk metrics")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric(
                "Volatility (ann.)",
                f"{risk['volatility_pct']:.1f}%",
                help="How much your daily returns swing around, scaled to a yearly "
                "figure. Higher = bumpier ride. It measures the size of the "
                "wiggles, not whether you made or lost money.",
            )
            r2.metric(
                "Return (ann.)",
                f"{risk['ann_return_pct']:.1f}%",
                help="Your time-weighted return expressed as a yearly rate. Because "
                "the history is short, this extrapolates a few months out to a "
                "full year, so it can look extreme in either direction.",
            )
            r3.metric(
                f"Sharpe (rf={risk['risk_free_pct']:.1f}%)",
                # Undefined without volatility; don't render a bare "nan".
                "—" if pd.isna(risk["sharpe"]) else f"{risk['sharpe']:.2f}",
                help="Return earned per unit of volatility ((annualised return − "
                "risk-free rate) ÷ volatility). A rough 'bang for your risk' "
                "score: >1 is decent, <0 means you did worse than cash. The "
                "risk-free rate is set by DEGIRO_RISK_FREE_PCT in .env. "
                "Unreliable over such a short window.",
            )
            r4.metric(
                "Max drawdown",
                f"{risk['max_drawdown_pct']:.1f}%",
                help="The worst peak-to-trough drop your portfolio has been through "
                "so far. A -10% here means at some point you were down 10% from "
                "a previous high before recovering.",
            )
            st.caption(
                f"Annualised from {risk['trading_days']} trading days of daily TWR "
                f"returns (out of {risk['days']} calendar days) — "
                "**noisy over such a short history**; more meaningful with >1 year. "
                "*Annualised* means scaled to a per-year figure; *TWR* (time-weighted "
                "return) strips out the effect of your deposits and withdrawals so "
                "the numbers reflect the investments themselves, not your cash timing."
            )

        # Correlation / effective overlap
        st.subheader("Correlation of daily returns")
        st.caption(
            "How similarly your holdings move (effective overlap). True constituent-level "
            "overlap isn't available without each fund's holdings, so this empirical view "
            "is the honest proxy. Note: based on a short (~3-month) history, so it's noisy."
        )
        corr = data["correlation"]
        if corr.empty or corr.shape[0] < 2:
            st.info("Not enough price history for a correlation matrix yet.")
        else:
            short = {c: (c[:24] + "…") if len(c) > 25 else c for c in corr.columns}
            cshort = corr.rename(index=short, columns=short)
            cfig = px.imshow(
                cshort, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1, aspect="auto", height=460
            )
            st.plotly_chart(cfig, width="stretch")

    # --- Holdings ---
    with tabs[2]:
        holdings = data["holdings"]
        if holdings.empty:
            st.info("No current positions.")
        else:
            st.dataframe(
                holdings,
                width="stretch",
                hide_index=True,
                column_config={
                    "size": st.column_config.NumberColumn("Size", format="%.4f"),
                    # DEGIRO quotes this in the product's own currency (see the currency
                    # column), which is not necessarily the account's base currency.
                    "price": st.column_config.NumberColumn("Price (quote ccy)", format="%.2f"),
                    "value": st.column_config.NumberColumn(f"Value ({base})", format="%.2f"),
                },
            )

        _holdings_detail(data, base)

    # --- Transactions & Income ---
    with tabs[3]:
        st.subheader("Transaction ledger")
        tx = data["transactions"]
        if tx.empty:
            st.info("No transactions.")
        else:
            query = st.text_input("Filter by name/symbol").strip().lower()
            if query:
                # regex=False: fund names contain parentheses ("... USD (Acc)"), so a
                # regex search silently matches nothing on "(acc)" and raises outright
                # on an unbalanced one, taking the tab down with it.
                mask = tx["name"].fillna("").str.lower().str.contains(query, regex=False) | tx["symbol"].fillna(
                    ""
                ).str.lower().str.contains(query, regex=False)
                tx = tx[mask]
            st.dataframe(tx, width="stretch", hide_index=True)

        st.subheader("P&L per transaction")
        tpnl = data["transaction_pnl"]
        if tpnl.empty:
            st.info("No transactions.")
        else:
            st.caption(
                "FIFO-matched. A buy carries unrealised P/L on the part of its lot still "
                "held; a sell carries the realised gain of that disposal. Both legs are "
                "net of transaction fees."
            )
            money = st.column_config.NumberColumn(format="%.2f")
            st.dataframe(
                tpnl[analytics.TRANSACTION_PNL_COLUMNS],
                width="stretch",
                hide_index=True,
                column_config={
                    "unit_price": st.column_config.NumberColumn(f"Unit price ({base})", format="%.4f"),
                    "cash_flow": st.column_config.NumberColumn(f"Cash flow ({base})", format="%.2f"),
                    "closed_quantity": st.column_config.NumberColumn("Closed qty", format="%.2f"),
                    "open_quantity": st.column_config.NumberColumn("Still held", format="%.2f"),
                    "realized": st.column_config.NumberColumn(f"Realised ({base})", format="%.2f"),
                    "unrealized": st.column_config.NumberColumn(f"Unrealised ({base})", format="%.2f"),
                    "total_pnl": st.column_config.NumberColumn(f"Total P/L ({base})", format="%.2f"),
                    "quantity": money,
                },
            )
            m1, m2, m3 = st.columns(3)
            m1.metric(f"Realised ({base})", f"{tpnl['realized'].sum():,.2f}")
            m2.metric(f"Unrealised ({base})", f"{tpnl['unrealized'].sum():,.2f}")
            m3.metric(f"Combined ({base})", f"{tpnl['total_pnl'].sum():,.2f}")

            rec = data["pnl_reconciliation"]
            if rec:
                st.caption(
                    f"Bridge to the Overview's Total P/L: realised {rec['realized']:,.2f} "
                    f"+ unrealised {rec['unrealized']:,.2f} + dividends {rec['dividends']:,.2f} "
                    f"+ other cash credits {rec['other']:,.2f} = {rec['total_pnl']:,.2f} {base}. "
                    "Dividends, interest and rebates are cash movements rather than trades, so "
                    "they cannot be attributed to a transaction row and sit outside this table."
                )

            lm = data["lot_matches"]
            if not lm.empty:
                with st.expander("Which purchase supplied each sold share (FIFO lot matches)"):
                    st.dataframe(
                        lm.drop(columns=["sell_tx_id", "buy_tx_id"]),
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "buy_unit_price": st.column_config.NumberColumn(f"Buy price ({base})", format="%.4f"),
                            "sell_unit_price": st.column_config.NumberColumn(f"Sell price ({base})", format="%.4f"),
                            "gain": st.column_config.NumberColumn(f"Gain ({base})", format="%.2f"),
                            "holding_days": st.column_config.NumberColumn("Held (days)", format="%d"),
                        },
                    )

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Dividends")
            _income_section(data["dividends"], base, cumulative=True)
        with c2:
            st.subheader("Fees")
            _income_section(data["fees"], base, cumulative=False)

        st.subheader("Trailing dividend yield by holding")
        dy = data["dividend_yield"]
        if dy.empty:
            st.info("No holdings/dividends yet.")
        else:
            st.dataframe(
                dy,
                width="stretch",
                hide_index=True,
                column_config={
                    "dividends": st.column_config.NumberColumn(f"Dividends ({base})", format="%.2f"),
                    "value": st.column_config.NumberColumn(f"Value ({base})", format="%.2f"),
                    "yield_pct": st.column_config.NumberColumn("Yield %", format="%.2f"),
                },
            )
            st.caption(
                "Trailing = dividends received so far ÷ current value. Not annualised "
                "(your history is short), so treat as 'received to date', not a forward yield."
            )

        st.subheader("Upcoming payments")
        up = data["upcoming_payments"]
        if up.empty:
            st.info("None reported by DEGIRO (or not yet fetched — run a full sync).")
        else:
            st.dataframe(up, width="stretch", hide_index=True)

    # --- Tax (NL Box 3) ---
    with tabs[4]:
        _tax_tab(data, base)

    # --- Data ---
    with tabs[5]:
        _data_tab(data, base)


def _holdings_detail(data, base):
    """Per-holding detail + allocation/TER breakdowns (moved here from the old
    Overview and Analysis tabs)."""
    # Per-holding value over time
    st.subheader("Per-holding value over time")
    ph = data["position_history"]
    if ph.empty:
        st.info("No per-holding history yet — run sync.")
    else:
        st.plotly_chart(
            px.line(
                ph,
                x="date",
                y="value",
                color="name",
                height=400,
                labels={"value": f"Value ({base})", "name": "Holding"},
            ),
            width="stretch",
        )

    # Per-holding return over time
    st.subheader("Per-holding return over time")
    prh = data["position_return_history"]
    if prh.empty:
        st.info("No per-holding return history yet — run sync.")
    else:
        fig_rh = px.line(
            prh,
            x="date",
            y="return_pct",
            color="name",
            height=400,
            labels={"return_pct": "Return %", "name": "Holding"},
        )
        fig_rh.add_hline(y=0, line_dash="dot", line_color="gray")
        fig_rh.update_layout(yaxis_ticksuffix="%")
        st.plotly_chart(fig_rh, width="stretch")

    # Per-holding return (value vs cost)
    st.subheader("Per-holding return (value vs cost)")
    perf = data["position_performance"]
    if perf.empty:
        st.info("No per-holding performance yet — run sync.")
    else:
        perf = perf.copy()
        perf["color"] = perf["return_pct"].apply(lambda x: "gain" if x >= 0 else "loss")
        fig_perf = px.bar(
            perf,
            x="return_pct",
            y="name",
            orientation="h",
            height=360,
            color="color",
            color_discrete_map={"gain": "#2e9e5b", "loss": "#d6455d"},
            labels={"return_pct": "Return %", "name": ""},
            text_auto=".1f",
        )
        fig_perf.update_layout(showlegend=False, yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_perf, width="stretch")
        st.dataframe(
            perf[["name", "cost", "value", "pnl", "return_pct"]],
            width="stretch",
            hide_index=True,
            column_config={
                "cost": st.column_config.NumberColumn(f"Cost ({base})", format="%.2f"),
                "value": st.column_config.NumberColumn(f"Value ({base})", format="%.2f"),
                "pnl": st.column_config.NumberColumn(f"P/L ({base})", format="%.2f"),
                "return_pct": st.column_config.NumberColumn("Return %", format="%.2f"),
            },
        )

    # Allocation + TER breakdowns (classification-based)
    cls = data["classification"]
    if cls.empty:
        st.info("No classified holdings yet — run a full sync for allocation & TER breakdowns.")
        return

    st.subheader("Allocation breakdown")
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        by_asset = cls.groupby("asset_class", as_index=False)["value"].sum()
        st.plotly_chart(
            px.pie(by_asset, names="asset_class", values="value", title="By asset class", hole=0.4),
            width="stretch",
        )
    with a2:
        by_cat = cls.groupby("category", as_index=False)["value"].sum()
        st.plotly_chart(
            px.pie(by_cat, names="category", values="value", title="Core vs satellite", hole=0.4),
            width="stretch",
        )
    with a3:
        by_region = cls.groupby("region", as_index=False)["value"].sum()
        st.plotly_chart(px.pie(by_region, names="region", values="value", title="By region", hole=0.4), width="stretch")
    with a4:
        st.plotly_chart(px.pie(cls, names="theme", values="value", title="By theme", hole=0.4), width="stretch")
    st.caption(
        "Classification from holdings_meta.yml — edit that file to reclassify. "
        "Asset class separates bonds from equity; region reflects each fund's mandate "
        "(S&P 500 = US, MSCI Europe = Europe; thematic funds are global)."
    )

    st.subheader("Costs — Total Expense Ratio (TER)")
    ter = data["ter_summary"]
    if ter:
        m1, m2, m3 = st.columns(3)
        m1.metric("Weighted avg TER", f"{ter['weighted_ter']:.3f}%")
        m2.metric("Est. annual fund cost", f"{ter['annual_cost']:,.2f} {base}")
        m3.metric("Coverage", f"{ter['coverage_pct']:.0f}%")
    cost_tbl = cls.assign(annual_cost=cls["value"] * cls["ter"] / 100)
    st.dataframe(
        cost_tbl[["name", "weight", "ter", "annual_cost"]],
        width="stretch",
        hide_index=True,
        column_config={
            "weight": st.column_config.NumberColumn("Weight %", format="%.1f"),
            "ter": st.column_config.NumberColumn("TER %", format="%.2f"),
            "annual_cost": st.column_config.NumberColumn(f"Annual cost ({base})", format="%.2f"),
        },
    )


def _data_tab(data, base):
    st.subheader("Data health")
    with store.connection() as conn:
        last_sync = store.get_meta(conn, "last_sync", "never")
        reports_fetched = store.get_meta(conn, "reports_fetched", "never")
    prices_df = store.read_df("prices")
    last_price = prices_df["date"].max() if not prices_df.empty else "n/a"

    h1, h2, h3 = st.columns(3)
    h1.metric("Last sync", str(last_sync))
    h2.metric("Reports fetched", str(reports_fetched))
    h3.metric("Latest price date", str(last_price))

    # Unresolved tickers + reconciliation status
    products = store.read_df("products")
    _, unresolved = _resolve_unresolved(products)
    if unresolved:
        st.warning(f"{len(unresolved)} product(s) without a resolved price ticker — add them to tickers.yml:")
        st.dataframe(pd.DataFrame(unresolved), width="stretch", hide_index=True)
    else:
        st.success("All products have a resolved price ticker. ✓")

    fresh = data["price_freshness"]
    if fresh.get("lag_days") is not None and fresh["lag_days"] > 1:
        st.warning(
            f"Cached prices end **{fresh['latest_price']}**, {fresh['lag_days']} days before the "
            f"last sync ({fresh['last_sync']}). Recent days are valued at the last close "
            "available — re-run a sync once Yahoo has published them."
        )

    # A resolved ticker is not a correct ticker: auto-resolution has silently matched a
    # different security on another exchange and backfilled plausible wrong prices.
    st.markdown("**Price series vs the prices you actually traded at**")
    st.caption(
        "Each holding's stored close on a trade date, compared with that trade's price. "
        "A few tenths of a percent is intraday drift; a large gap means the ticker "
        "resolved to the wrong security, so fix it in tickers.yml and re-run "
        "`sync.py --offline`."
    )
    check = data["ticker_check"]
    if check.empty:
        st.info("No transactions to validate prices against yet.")
    else:
        bad = check[check["status"] != "ok"]
        if bad.empty:
            st.success("Every price series matches its transaction prices. ✓")
        else:
            st.warning(f"{len(bad)} holding(s) need a look — see the status column.")
        st.dataframe(
            check,
            width="stretch",
            hide_index=True,
            column_config={
                "rows": st.column_config.NumberColumn("Price rows", format="%d"),
                "trades_checked": st.column_config.NumberColumn("Trades checked", format="%d"),
                "worst_gap_pct": st.column_config.NumberColumn("Worst gap %", format="%.2f"),
            },
        )

    cc = data["crosscheck"]
    if not cc.empty:
        if (cc["match"] == "✓").all():
            st.success("All figures reconcile with DEGIRO's official reports. ✓")
        else:
            st.warning("Some figures differ from official reports — see the Tax tab.")

    # Exports
    st.subheader("Export")
    st.caption("Download your data as CSV.")
    e1, e2, e3 = st.columns(3)
    _download(e1, "Daily value history", analytics.daily_value(), "degiro_daily_value.csv")
    _download(e2, "Current holdings", data["holdings"], "degiro_holdings.csv")
    _download(e3, "Transactions", data["transactions"], "degiro_transactions.csv")
    e4, e5, _ = st.columns(3)
    _download(e4, "P&L per transaction", data["transaction_pnl"], "degiro_transaction_pnl.csv")
    _download(e5, "FIFO lot matches", data["lot_matches"], "degiro_lot_matches.csv")


def _resolve_unresolved(products):
    from degiro_explorer import prices as _prices

    if products.empty:
        return {}, []
    return _prices.resolve_tickers(products)


def _download(col, label, df, filename):
    if df is None or df.empty:
        col.button(label, disabled=True)
        return
    col.download_button(label, df.to_csv(index=False).encode("utf-8"), file_name=filename, mime="text/csv")


def _income_section(df, base, cumulative: bool):
    """Render a sparse income series (dividends/fees): table-first, monthly bars,
    plus an optional cumulative line (meaningful only for accruing income)."""
    if df.empty:
        st.info("None found.")
        return

    # Total over the base-currency column: `amount` is in each movement's own currency,
    # so summing it across currencies would add unlike units.
    st.metric("Total", f"{df['amount_base'].sum():,.2f} {base}")

    # Table first — clearest for sparse, discrete payments.
    st.dataframe(
        df.sort_values("month"),
        width="stretch",
        hide_index=True,
        column_config={
            "amount": st.column_config.NumberColumn("Amount (own ccy)", format="%.2f"),
            "amount_base": st.column_config.NumberColumn(f"Amount ({base})", format="%.2f"),
        },
    )

    # Monthly bars: discrete amount per period. Only colour by currency if >1 currency.
    multi_ccy = df["currency"].nunique() > 1
    bar = px.bar(df, x="month", y="amount_base", height=300, color="currency" if multi_ccy else None)
    bar.update_layout(showlegend=multi_ccy)
    st.plotly_chart(bar, width="stretch")

    # Cumulative line — the one line view that makes sense (running total over time).
    if cumulative:
        cum = df.sort_values("month").copy()
        cum["cumulative"] = cum["amount_base"].cumsum()
        st.plotly_chart(
            px.line(
                cum, x="month", y="cumulative", markers=True, height=260, labels={"cumulative": f"Cumulative ({base})"}
            ),
            width="stretch",
        )


def _tax_tab(data, base):
    st.warning(
        "ℹ️ **Informational only — not tax advice.** Figures are organised from "
        "unofficial DEGIRO data to help you understand/prepare; verify everything and "
        "consult the [Belastingdienst](https://www.belastingdienst.nl) or an adviser."
    )
    st.markdown(
        "In the Netherlands, private investors are **not** taxed on realised gains or "
        "actual dividends. Instead **Box 3** taxes a *deemed* return on the **value of "
        "your assets on 1 January** (the *peildatum*), above a tax-free allowance."
    )

    # 0. Cross-check against DEGIRO's official reports
    st.subheader("Cross-check vs official DEGIRO reports")
    cc = data["crosscheck"]
    if cc.empty:
        st.info("No official reports yet — run a full `python scripts/sync.py` to pull them.")
    else:
        with store.connection() as conn:
            fetched = store.get_meta(conn, "reports_fetched", "unknown")
        st.caption(
            f"Official reports fetched: **{fetched}**. App figures vs DEGIRO's own account statement & position report."
        )
        st.dataframe(
            cc,
            width="stretch",
            hide_index=True,
            column_config={
                "app": st.column_config.NumberColumn(f"App ({base})", format="%.2f"),
                "official": st.column_config.NumberColumn(f"Official ({base})", format="%.2f"),
                "delta": st.column_config.NumberColumn(f"Δ ({base})", format="%.2f"),
            },
        )
        if (cc["match"] == "✓").all():
            st.success("All figures reconcile with DEGIRO's official reports. ✓")
        else:
            st.warning("Some figures differ from the official reports — see Δ column.")

        cch = data["crosscheck_holdings"]
        if not cch.empty:
            st.markdown("**Per-holding reconciliation** (value vs position report, by ISIN)")
            st.dataframe(
                cch,
                width="stretch",
                hide_index=True,
                column_config={
                    "app": st.column_config.NumberColumn(f"App ({base})", format="%.2f"),
                    "official": st.column_config.NumberColumn(f"Official ({base})", format="%.2f"),
                    "delta": st.column_config.NumberColumn(f"Δ ({base})", format="%.2f"),
                },
            )
            if (cch["match"] == "✓").all():
                st.success("Every holding matches the position report. ✓")

        pos_path = reports.position_report_path()
        acct_path = reports.account_report_path()
        d1, d2 = st.columns(2)
        if acct_path.exists():
            d1.download_button(
                "⬇ Account statement (CSV)",
                acct_path.read_bytes(),
                file_name="degiro_account_report.csv",
                mime="text/csv",
            )
        if pos_path.exists():
            d2.download_button(
                "⬇ Position report (CSV)",
                pos_path.read_bytes(),
                file_name="degiro_position_report.csv",
                mime="text/csv",
            )

    # 1. Box 3 reference values (value on 1 Jan)
    st.subheader("Box 3 reference value (1 January)")
    ref = data["box3_reference"]
    if ref.empty:
        st.info(
            "No 1-January snapshot falls inside your history yet (the account opened "
            "mid-year), so this portfolio generates no Box 3 until the next peildatum. "
            "Use the estimator below with your projected 1-Jan value."
        )
    else:
        st.dataframe(
            ref,
            width="stretch",
            hide_index=True,
            column_config={"value": st.column_config.NumberColumn(f"Value 1-Jan ({base})", format="%.2f")},
        )

    # 2. Rough Box 3 estimate
    st.subheader("Rough Box 3 estimate")
    st.caption(
        "Parameters change yearly — pick a tax year to load the official figures, then "
        "edit them if needed. Always verify against the Belastingdienst: Box 3 is "
        "mid-reform and announced figures get revised before they are enacted."
    )
    years = sorted(analytics.BOX3_PARAMS, reverse=True)
    y0, _ = st.columns([1, 3])
    tax_year = y0.selectbox("Tax year", years, index=0)
    params = analytics.box3_params(tax_year)
    if params.provisional:
        st.warning(
            f"⚠️ The {tax_year} figures are **provisional** — announced but not yet enacted, "
            f"and the tax-free allowance has not been published ({tax_year - 1}'s is used as a "
            "placeholder). Treat this year's estimate as indicative only."
        )

    latest_value = float(data["daily"]["total_value"].iloc[-1]) if not data["daily"].empty else 0.0
    c1, c2, c3 = st.columns(3)
    value = c1.number_input(
        f"Asset value on 1 Jan ({base})",
        min_value=0.0,
        value=round(latest_value, 2),
        step=1000.0,
        help="Defaults to your latest total value as a proxy for the next peildatum.",
    )
    deemed = c2.number_input(
        "Deemed return % (investments)",
        min_value=0.0,
        value=params.deemed_return_pct,
        step=0.01,
        key=f"box3_deemed_{tax_year}",
    )
    rate = c3.number_input("Tax rate %", min_value=0.0, value=params.rate_pct, step=0.5, key=f"box3_rate_{tax_year}")
    c4, c5 = st.columns(2)
    partners = c4.checkbox("Fiscal partners (double allowance)", value=False)
    allowance = c5.number_input(
        "Tax-free allowance (€, per person)",
        min_value=0.0,
        value=params.allowance,
        step=100.0,
        key=f"box3_allowance_{tax_year}",
    )
    if partners:
        allowance *= 2

    est = analytics.box3_tax(value, deemed, allowance, rate)
    m1, m2, m3 = st.columns(3)
    m1.metric("Taxable base", f"{est['taxable_base']:,.0f} {base}")
    m2.metric("Deemed income", f"{est['deemed_income']:,.0f} {base}")
    m3.metric("Estimated Box 3 tax", f"{est['tax']:,.0f} {base}")
    if est["taxable_base"] == 0:
        st.success("Below the tax-free allowance → estimated Box 3 tax is €0.")

    # 3. Dividends (income context) — creditable dividend withholding in NL
    st.subheader("Dividends received")
    div = data["dividends"]
    if div.empty:
        st.info("No dividends recorded.")
    else:
        st.metric("Total dividends", f"{div['amount_base'].sum():,.2f} {base}")
        st.caption(
            "No Dutch dividend withholding (*dividendbelasting*) is recorded in your "
            "data — typical for Irish-domiciled ETFs. Any NL withholding is creditable."
        )
        st.dataframe(div, width="stretch", hide_index=True)

    # 4. Realized gains — informational (not taxed in NL Box 3)
    st.subheader("Realised gains (informational)")
    rg = data["realized_gains"]
    if rg.empty:
        st.info("No disposals yet — you haven't sold anything. (Not taxed in NL Box 3 anyway.)")
    else:
        st.caption(
            "FIFO (First-In, First-Out) matched. Not taxed for NL private investors "
            "under Box 3 — shown for completeness."
        )
        st.dataframe(rg, width="stretch", hide_index=True)

    # 5. Legal / domicile notes
    st.subheader("Notes")
    st.markdown(
        "- **All your ETFs are Irish-domiciled** (ISIN `IE…`), generally efficient for "
        "withholding tax for EU investors.\n"
        "- This is an **unofficial** integration; data may differ from official DEGIRO "
        "tax statements — DEGIRO provides an annual report for the Belastingdienst.\n"
        "- Box 3 is under reform toward a *werkelijk rendement* (actual-return) system; "
        "check current rules each year."
    )


if __name__ == "__main__":
    main()
