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
    }


def main() -> None:
    st.title("📈 DEGIRO Explorer")

    if not settings.db_file.exists():
        st.warning("No database yet. Run `python scripts/sync.py` first.")
        return

    with store.connection() as conn:
        base = store.get_meta(conn, "base_currency", "EUR")
        last_sync = store.get_meta(conn, "last_sync", "never")
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

    data = _load()
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
        st.plotly_chart(fig, use_container_width=True)

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
        st.plotly_chart(cgfig, use_container_width=True)
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
            st.plotly_chart(fig, use_container_width=True)
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
                st.plotly_chart(plfig, use_container_width=True)
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
            st.plotly_chart(ddfig, use_container_width=True)
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
                "Sharpe (rf=0)",
                f"{risk['sharpe']:.2f}",
                help="Return earned per unit of volatility (annualised return ÷ "
                "volatility, assuming a 0% risk-free rate). A rough 'bang for "
                "your risk' score: >1 is decent, <0 means you lost money. "
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
                f"Annualised from ~{risk['days']} days of daily TWR returns — "
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
            st.plotly_chart(cfig, use_container_width=True)

    # --- Holdings ---
    with tabs[2]:
        holdings = data["holdings"]
        if holdings.empty:
            st.info("No current positions.")
        else:
            st.dataframe(
                holdings,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "size": st.column_config.NumberColumn("Size", format="%.4f"),
                    "price": st.column_config.NumberColumn(f"Price ({base})", format="%.2f"),
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
                mask = tx["name"].fillna("").str.lower().str.contains(query) | tx["symbol"].fillna(
                    ""
                ).str.lower().str.contains(query)
                tx = tx[mask]
            st.dataframe(tx, use_container_width=True, hide_index=True)

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
                use_container_width=True,
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
            st.dataframe(up, use_container_width=True, hide_index=True)

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
            use_container_width=True,
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
        st.plotly_chart(fig_rh, use_container_width=True)

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
        st.plotly_chart(fig_perf, use_container_width=True)
        st.dataframe(
            perf[["name", "cost", "value", "pnl", "return_pct"]],
            use_container_width=True,
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
    a1, a2, a3 = st.columns(3)
    with a1:
        by_cat = cls.groupby("category", as_index=False)["value"].sum()
        st.plotly_chart(
            px.pie(by_cat, names="category", values="value", title="Core vs satellite", hole=0.4),
            use_container_width=True,
        )
    with a2:
        by_region = cls.groupby("region", as_index=False)["value"].sum()
        st.plotly_chart(
            px.pie(by_region, names="region", values="value", title="By region", hole=0.4), use_container_width=True
        )
    with a3:
        st.plotly_chart(
            px.pie(cls, names="theme", values="value", title="By theme", hole=0.4), use_container_width=True
        )
    st.caption(
        "Classification from holdings_meta.yml — edit that file to reclassify. "
        "Region reflects each fund's mandate (S&P 500 = US, MSCI Europe = Europe; "
        "thematic funds are global)."
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
        use_container_width=True,
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
        st.dataframe(pd.DataFrame(unresolved), use_container_width=True, hide_index=True)
    else:
        st.success("All products have a resolved price ticker. ✓")

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

    st.metric("Total", f"{df['amount'].sum():,.2f} {base}")

    # Table first — clearest for sparse, discrete payments.
    st.dataframe(
        df.sort_values("month"),
        use_container_width=True,
        hide_index=True,
        column_config={"amount": st.column_config.NumberColumn(f"Amount ({base})", format="%.2f")},
    )

    # Monthly bars: discrete amount per period. Only colour by currency if >1 currency.
    multi_ccy = df["currency"].nunique() > 1
    bar = px.bar(df, x="month", y="amount", height=300, color="currency" if multi_ccy else None)
    bar.update_layout(showlegend=multi_ccy)
    st.plotly_chart(bar, use_container_width=True)

    # Cumulative line — the one line view that makes sense (running total over time).
    if cumulative:
        cum = df.sort_values("month").copy()
        cum["cumulative"] = cum["amount"].cumsum()
        st.plotly_chart(
            px.line(
                cum, x="month", y="cumulative", markers=True, height=260, labels={"cumulative": f"Cumulative ({base})"}
            ),
            use_container_width=True,
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
            use_container_width=True,
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
                use_container_width=True,
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
            use_container_width=True,
            hide_index=True,
            column_config={"value": st.column_config.NumberColumn(f"Value 1-Jan ({base})", format="%.2f")},
        )

    # 2. Rough Box 3 estimate
    st.subheader("Rough Box 3 estimate")
    st.caption(
        "Parameters change yearly — defaults shown for reference, edit as needed. "
        "2025: investments 5.88%, allowance €57,684 (single); 2026 allowance €51,396."
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
    deemed = c2.number_input("Deemed return % (investments)", min_value=0.0, value=5.88, step=0.01)
    rate = c3.number_input("Tax rate %", min_value=0.0, value=36.0, step=0.5)
    c4, c5 = st.columns(2)
    partners = c4.checkbox("Fiscal partners (double allowance)", value=False)
    allowance = c5.number_input("Tax-free allowance (€)", min_value=0.0, value=51396.0, step=100.0)
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
        st.metric("Total dividends", f"{div['amount'].sum():,.2f}")
        st.caption(
            "No Dutch dividend withholding (*dividendbelasting*) is recorded in your "
            "data — typical for Irish-domiciled ETFs. Any NL withholding is creditable."
        )
        st.dataframe(div, use_container_width=True, hide_index=True)

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
        st.dataframe(rg, use_container_width=True, hide_index=True)

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
