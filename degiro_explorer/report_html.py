"""Build a standalone HTML snapshot of the portfolio, for saving or printing.

Everything is inlined: CSS in a <style> block, charts as hand-written SVG, no scripts
and no external requests. The point of the file is to still open correctly years from
now, from a backup folder, with no network -- so a Plotly bundle (megabytes of JS, or a
CDN link that eventually 404s) would defeat it. The charts here are simple enough that
an <svg> polyline says the same thing in a few hundred bytes.

Figures come from the same analytics functions the dashboard renders, so the report
cannot drift from the screen. `build_report(data=...)` accepts the dashboard's already
computed dict to avoid repeating the work.
"""

from __future__ import annotations

import html
from datetime import datetime

import pandas as pd

from . import analytics, reports, store


def build_report(data: dict | None = None, base: str | None = None) -> str:
    """Render the whole report as one self-contained HTML document."""
    with store.cached_reads():
        d = data if data is not None else _gather()
        if base is None:
            with store.connection() as conn:
                base = str(store.get_meta(conn, "base_currency", "EUR"))
                last_sync = str(store.get_meta(conn, "last_sync", "never"))
        else:
            with store.connection() as conn:
                last_sync = str(store.get_meta(conn, "last_sync", "never"))
        return _render(d, base, last_sync)


def _gather() -> dict:
    """Everything the report needs, when called outside the dashboard."""
    per_tx = analytics.transaction_pnl()
    return {
        "daily": analytics.daily_value(),
        "kpis": analytics.summary_kpis(),
        "holdings": analytics.current_holdings(),
        "classification": analytics.holdings_classification(),
        "position_performance": analytics.position_performance(),
        "performance_curves": analytics.performance_curves(),
        "benchmark_curves": analytics.benchmark_curves(),
        "risk_metrics": analytics.risk_metrics(),
        "drawdown": analytics.drawdown_series(),
        "transaction_pnl": per_tx,
        "pnl_reconciliation": analytics.pnl_reconciliation(per_tx),
        "dividends": analytics.dividends(),
        "fees": analytics.fees(),
        "dividend_yield": analytics.dividend_yield(),
        "upcoming_payments": analytics.upcoming_payments(),
        "realized_gains": analytics.realized_gains(),
        "ter_summary": analytics.ter_summary(),
        "box3_reference": analytics.box3_reference_values(),
        "crosscheck": reports.crosscheck(),
        "ticker_check": analytics.ticker_price_check(),
        "price_freshness": analytics.price_freshness(),
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _money(value, base: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "&mdash;"
    return f"{float(value):,.2f}{' ' + base if base else ''}"


def _pct(value, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "&mdash;"
    return f"{float(value):,.{digits}f}%"


def _sign_class(value) -> str:
    """Green/red class for a signed figure; neutral when it is missing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return "pos" if float(value) >= 0 else "neg"


def _kpi(label: str, value: str, note: str = "", cls: str = "") -> str:
    note_html = f'<div class="note">{note}</div>' if note else ""
    return (
        f'<div class="kpi"><div class="label">{_esc(label)}</div>'
        f'<div class="value {cls}">{value}</div>{note_html}</div>'
    )


def _table(rows: list[list[str]], headers: list[str], align_right: set[int] | None = None) -> str:
    right = align_right or set()
    head = "".join(f'<th class="{"r" if i in right else ""}">{_esc(h)}</th>' for i, h in enumerate(headers))
    body = []
    for row in rows:
        cells = "".join(f'<td class="{"r" if i in right else ""}">{c}</td>' for i, c in enumerate(row))
        body.append(f"<tr>{cells}</tr>")
    if not body:
        body.append(f'<tr><td colspan="{len(headers)}" class="muted">Nothing to show.</td></tr>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


# ---------------------------------------------------------------------------
# Charts (inline SVG, no scripts)
# ---------------------------------------------------------------------------


def _line_chart(series: dict[str, pd.Series], width: int = 900, height: int = 260) -> str:
    """Multi-series line chart over a shared date index.

    Hand-rolled so the file stays scriptless and tiny. Anything fancier belongs in the
    dashboard, which has Plotly and a browser to run it.
    """
    clean = {name: s.dropna() for name, s in series.items() if s is not None and not s.dropna().empty}
    if not clean:
        return '<p class="muted">No data to plot.</p>'

    pad_l, pad_r, pad_t, pad_b = 70, 12, 12, 26
    all_values = pd.concat(list(clean.values()))
    lo, hi = float(all_values.min()), float(all_values.max())
    if hi == lo:  # a flat series still deserves a line rather than a divide-by-zero
        hi, lo = hi + 1.0, lo - 1.0
    index = sorted({ts for s in clean.values() for ts in s.index})
    first, last = index[0], index[-1]
    span = (last - first).total_seconds() or 1.0

    def x(ts) -> float:
        return pad_l + (ts - first).total_seconds() / span * (width - pad_l - pad_r)

    def y(value: float) -> float:
        return pad_t + (hi - value) / (hi - lo) * (height - pad_t - pad_b)

    colours = ["#2e6fd6", "#2e9e5b", "#d6455d", "#a259c4"]
    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">']
    # Horizontal gridlines + value axis.
    for frac in (0.0, 0.5, 1.0):
        value = lo + (hi - lo) * frac
        yy = y(value)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" class="grid"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{yy + 4:.1f}" class="axis r">{value:,.0f}</text>')
    for i, s in enumerate(clean.values()):
        points = " ".join(f"{x(ts):.1f},{y(float(v)):.1f}" for ts, v in s.items())
        colour = colours[i % len(colours)]
        dash = ' stroke-dasharray="5 4"' if i else ""
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2"{dash}/>')
    parts.append(f'<text x="{pad_l}" y="{height - 6}" class="axis">{first.date().isoformat()}</text>')
    parts.append(f'<text x="{width - pad_r}" y="{height - 6}" class="axis end">{last.date().isoformat()}</text>')
    parts.append("</svg>")

    legend = " ".join(
        f'<span class="key"><i style="background:{colours[i % len(colours)]}"></i>{_esc(name)}</span>'
        for i, name in enumerate(clean)
    )
    return "".join(parts) + f'<div class="legend">{legend}</div>'


def _bar_chart(labels: list[str], values: list[float], suffix: str = "%") -> str:
    """Horizontal bars for signed values (returns) or positive ones (weights)."""
    if not labels:
        return '<p class="muted">No data to plot.</p>'
    scale = max((abs(v) for v in values), default=1.0) or 1.0
    rows = []
    for label, value in zip(labels, values, strict=False):
        width = abs(value) / scale * 100
        cls = "pos" if value >= 0 else "neg"
        rows.append(
            f'<div class="bar-row"><span class="bar-label">{_esc(label)}</span>'
            f'<span class="bar-track"><span class="bar {cls}" style="width:{width:.1f}%"></span></span>'
            f'<span class="bar-value {cls}">{value:,.2f}{suffix}</span></div>'
        )
    return f'<div class="bars">{"".join(rows)}</div>'


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _section_summary(d: dict, base: str) -> str:
    k = d.get("kpis") or {}
    curves = d.get("performance_curves")
    twr = None
    if curves is not None and not curves.empty:
        twr = float(curves["twr_pct"].iloc[-1])
    cards = [
        _kpi("Total value", _money(k.get("total_value"), base)),
        _kpi("Net invested", _money(k.get("net_invested"), base)),
        _kpi("Total P/L", _money(k.get("total_pnl"), base), cls=_sign_class(k.get("total_pnl"))),
        _kpi("Total return", _pct(k.get("total_return_pct")), cls=_sign_class(k.get("total_return_pct"))),
        _kpi("TWR", _pct(twr), "deposit-proof", cls=_sign_class(twr)),
        _kpi("Cash", _money(k.get("cash"), base)),
    ]
    return f'<section><h2>Summary</h2><div class="kpis">{"".join(cards)}</div></section>'


def _section_value(d: dict, base: str) -> str:
    daily = d.get("daily")
    if daily is None or daily.empty:
        return ""
    df = daily.set_index("date")
    chart = _line_chart({"Total value": df["total_value"], "Net invested": df["net_invested"]})
    return (
        f"<section><h2>Value over time ({_esc(base)})</h2>{chart}"
        '<p class="muted">The gap between the two lines is market growth: value above '
        "contributions.</p></section>"
    )


def _section_holdings(d: dict, base: str) -> str:
    cls = d.get("classification")
    perf = d.get("position_performance")
    if cls is None or cls.empty:
        return ""
    perf_by_name = {}
    if perf is not None and not perf.empty:
        perf_by_name = perf.set_index("name").to_dict("index")

    rows = []
    for _, h in cls.iterrows():
        p = perf_by_name.get(h["name"], {})
        rows.append(
            [
                _esc(h["name"]),
                _esc(h.get("isin")),
                _esc(h.get("asset_class")),
                _esc(h.get("region")),
                _money(h.get("value")),
                _pct(h.get("weight"), 1),
                _money(p.get("cost")),
                f'<span class="{_sign_class(p.get("pnl"))}">{_money(p.get("pnl"))}</span>',
                f'<span class="{_sign_class(p.get("return_pct"))}">{_pct(p.get("return_pct"))}</span>',
                _pct(h.get("ter"), 2) if pd.notna(h.get("ter")) else "&mdash;",
            ]
        )
    table = _table(
        rows,
        [
            "Holding",
            "ISIN",
            "Asset class",
            "Region",
            f"Value ({base})",
            "Weight",
            f"Cost ({base})",
            "P/L",
            "Return",
            "TER",
        ],
        align_right={4, 5, 6, 7, 8, 9},
    )

    bars = ""
    if perf is not None and not perf.empty:
        bars = "<h3>Return by holding</h3>" + _bar_chart(perf["name"].tolist(), [float(v) for v in perf["return_pct"]])

    ter = d.get("ter_summary") or {}
    ter_line = ""
    if ter:
        ter_line = (
            f'<p class="muted">Weighted average TER <b>{_pct(ter["weighted_ter"], 3)}</b> '
            f"&middot; estimated annual fund cost <b>{_money(ter['annual_cost'], base)}</b> "
            f"&middot; coverage {_pct(ter['coverage_pct'], 0)} of value.</p>"
        )
    alloc = _allocation_block(cls)
    return f"<section><h2>Holdings</h2>{table}{ter_line}{alloc}{bars}</section>"


def _allocation_block(cls: pd.DataFrame) -> str:
    """Weight by each classification axis, as bars."""
    blocks = []
    for column, title in (
        ("asset_class", "By asset class"),
        ("category", "Core vs satellite"),
        ("region", "By region"),
        ("theme", "By theme"),
    ):
        if column not in cls:
            continue
        grouped = cls.groupby(column)["value"].sum().sort_values(ascending=False)
        total = float(grouped.sum()) or 1.0
        weights = [float(v) / total * 100 for v in grouped]
        blocks.append(f'<div class="alloc"><h4>{_esc(title)}</h4>{_bar_chart(list(grouped.index), weights)}</div>')
    return f'<h3>Allocation</h3><div class="alloc-grid">{"".join(blocks)}</div>' if blocks else ""


def _section_performance(d: dict, base: str) -> str:
    risk = d.get("risk_metrics") or {}
    curves = d.get("performance_curves")
    bench = d.get("benchmark_curves")
    parts = []

    if risk:
        sharpe = risk.get("sharpe")
        cards = [
            _kpi("Volatility (ann.)", _pct(risk.get("volatility_pct"), 1)),
            _kpi("Return (ann.)", _pct(risk.get("ann_return_pct"), 1), cls=_sign_class(risk.get("ann_return_pct"))),
            _kpi(
                f"Sharpe (rf={risk.get('risk_free_pct', 0):.1f}%)",
                "&mdash;" if sharpe is None or pd.isna(sharpe) else f"{float(sharpe):.2f}",
            ),
            _kpi("Max drawdown", _pct(risk.get("max_drawdown_pct"), 1), cls="neg"),
        ]
        parts.append(f'<div class="kpis">{"".join(cards)}</div>')
        parts.append(
            f'<p class="muted">Annualised from {risk.get("trading_days", 0)} trading days out of '
            f"{risk.get('days', 0)} calendar days &mdash; noisy over a short history.</p>"
        )

    if curves is not None and not curves.empty:
        series = {"Portfolio (TWR)": curves.set_index("date")["twr_pct"]}
        if bench is not None and not bench.empty:
            for name, grp in bench.groupby("benchmark"):
                series[str(name)] = grp.set_index("date")["return_pct"]
        parts.append("<h3>Return vs benchmarks (%)</h3>")
        parts.append(_line_chart(series))
        latest = [f"Portfolio {_pct(curves['twr_pct'].iloc[-1])}"]
        if bench is not None and not bench.empty:
            for name, grp in bench.groupby("benchmark"):
                latest.append(f"{_esc(name)} {_pct(grp['return_pct'].iloc[-1])}")
        parts.append(f'<p class="muted">Latest: {" &middot; ".join(latest)}.</p>')

    dd = d.get("drawdown")
    if dd is not None and not dd.empty:
        parts.append("<h3>Drawdown (%)</h3>")
        parts.append(_line_chart({"Drawdown": dd.set_index("date")["drawdown_pct"]}))

    return f"<section><h2>Performance &amp; risk</h2>{''.join(parts)}</section>" if parts else ""


def _section_pnl(d: dict, base: str) -> str:
    rec = d.get("pnl_reconciliation") or {}
    if not rec:
        return ""
    rows = [
        [
            "Realised (closed disposals, FIFO)",
            f'<span class="{_sign_class(rec["realized"])}">{_money(rec["realized"])}</span>',
        ],
        [
            "Unrealised (open positions)",
            f'<span class="{_sign_class(rec["unrealized"])}">{_money(rec["unrealized"])}</span>',
        ],
        ["Dividends", _money(rec["dividends"])],
        ["Other cash credits (interest, rebates)", _money(rec["other"])],
        ["<b>Total P/L</b>", f'<b class="{_sign_class(rec["total_pnl"])}">{_money(rec["total_pnl"])}</b>'],
    ]
    table = _table(rows, ["Component", f"Amount ({base})"], align_right={1})
    note = (
        '<p class="muted">Dividends, interest and rebates are cash movements rather than '
        "trades, so they cannot be attributed to a transaction &mdash; which is why the "
        "per-transaction ledger totals less than the headline P/L.</p>"
    )
    return f"<section><h2>Profit &amp; loss</h2>{table}{note}</section>"


def _section_income(d: dict, base: str) -> str:
    div, fee = d.get("dividends"), d.get("fees")
    div_total = float(div["amount_base"].sum()) if div is not None and not div.empty else 0.0
    fee_total = float(fee["amount_base"].sum()) if fee is not None and not fee.empty else 0.0
    cards = [
        _kpi("Dividends received", _money(div_total, base), cls="pos" if div_total else ""),
        _kpi("Fees paid", _money(fee_total, base), cls="neg" if fee_total else ""),
    ]
    parts = [f'<div class="kpis">{"".join(cards)}</div>']

    dy = d.get("dividend_yield")
    if dy is not None and not dy.empty:
        rows = [
            [_esc(r["name"]), _money(r["dividends"]), _money(r["value"]), _pct(r["yield_pct"])]
            for _, r in dy.iterrows()
        ]
        parts.append("<h3>Trailing yield by holding</h3>")
        parts.append(
            _table(rows, ["Holding", f"Dividends ({base})", f"Value ({base})", "Yield"], align_right={1, 2, 3})
        )
        parts.append('<p class="muted">Received to date &divide; current value &mdash; not annualised.</p>')

    up = d.get("upcoming_payments")
    if up is not None and not up.empty:
        rows = [
            [_esc(r["pay_date"]), _esc(r["product"]), _esc(r["currency"]), _money(r["amount"]), _esc(r["description"])]
            for _, r in up.iterrows()
        ]
        parts.append("<h3>Upcoming payments</h3>")
        parts.append(_table(rows, ["Pay date", "Product", "Currency", "Amount", "Description"], align_right={3}))

    return f"<section><h2>Income &amp; costs</h2>{''.join(parts)}</section>"


def _section_tax(d: dict, base: str) -> str:
    ref = d.get("box3_reference")
    parts = [
        "<p>The Netherlands does not tax realised gains or actual dividends for private "
        "investors. <b>Box 3</b> taxes a deemed return on the value of your assets on "
        "<b>1 January</b> (the <i>peildatum</i>), above a tax-free allowance.</p>"
    ]
    if ref is not None and not ref.empty:
        rows = [[_esc(r["year"]), _esc(r["reference_date"]), _money(r["value"])] for _, r in ref.iterrows()]
        parts.append(_table(rows, ["Year", "Reference date", f"Value ({base})"], align_right={2}))
    else:
        parts.append(
            '<p class="muted">No 1 January falls inside the reconstructed history yet, so '
            "this portfolio generates no Box 3 liability for the current tax year.</p>"
        )

    year = analytics.LATEST_BOX3_YEAR
    params = analytics.box3_params(year)
    kpi = d.get("kpis") or {}
    est = analytics.box3_tax(
        float(kpi.get("total_value", 0.0)), params.deemed_return_pct, params.allowance, params.rate_pct
    )
    provisional = (
        ' <span class="warn">Provisional &mdash; announced but not enacted.</span>' if params.provisional else ""
    )
    parts.append(
        f"<h3>Indicative {year} estimate</h3>"
        f'<p class="muted">Using today\'s total value as a proxy for the next peildatum, at '
        f"{params.deemed_return_pct:.2f}% deemed return, {_money(params.allowance)} allowance and "
        f"{params.rate_pct:.0f}% rate.{provisional}</p>"
    )
    rows = [
        ["Taxable base", _money(est["taxable_base"])],
        ["Deemed income", _money(est["deemed_income"])],
        ["<b>Estimated tax</b>", f"<b>{_money(est['tax'])}</b>"],
    ]
    parts.append(_table(rows, ["Item", f"Amount ({base})"], align_right={1}))

    rg = d.get("realized_gains")
    if rg is not None and not rg.empty:
        rows = [
            [
                _esc(r["date"]),
                _esc(r["name"]),
                f"{float(r['quantity']):,.2f}",
                _money(r["proceeds"]),
                _money(r["cost"]),
                f'<span class="{_sign_class(r["gain"])}">{_money(r["gain"])}</span>',
            ]
            for _, r in rg.iterrows()
        ]
        parts.append("<h3>Realised gains (informational &mdash; not taxed in Box 3)</h3>")
        parts.append(
            _table(rows, ["Date", "Holding", "Quantity", "Proceeds", "Cost", "Gain"], align_right={2, 3, 4, 5})
        )
    return f"<section><h2>Tax &mdash; NL Box 3</h2>{''.join(parts)}</section>"


def _section_health(d: dict, last_sync: str) -> str:
    parts = []
    fresh = d.get("price_freshness") or {}
    cc = d.get("crosscheck")
    if cc is not None and not cc.empty:
        rows = [
            [_esc(r["metric"]), _money(r["app"]), _money(r["official"]), _money(r["delta"]), _esc(r["match"])]
            for _, r in cc.iterrows()
        ]
        parts.append("<h3>Cross-check vs official DEGIRO reports</h3>")
        parts.append(_table(rows, ["Metric", "App", "Official", "Delta", ""], align_right={1, 2, 3, 4}))

    check = d.get("ticker_check")
    if check is not None and not check.empty:
        bad = check[check["status"] != "ok"]
        if bad.empty:
            parts.append('<p class="ok">Every price series matches the prices actually traded.</p>')
        else:
            rows = [
                [_esc(r["name"]), _esc(r["ticker"]), _pct(r["worst_gap_pct"]), _esc(r["status"])]
                for _, r in bad.iterrows()
            ]
            parts.append("<h3>Price series needing attention</h3>")
            parts.append(_table(rows, ["Holding", "Ticker", "Worst gap", "Status"], align_right={2}))

    parts.append(
        f'<p class="muted">Last sync <b>{_esc(last_sync)}</b> &middot; latest cached price '
        f"<b>{_esc(fresh.get('latest_price') or 'n/a')}</b>.</p>"
    )
    return f"<section><h2>Data health</h2>{''.join(parts)}</section>"


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px; font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       color: #1b1f24; background: #fff; }
.wrap { max-width: 1040px; margin: 0 auto; }
header { border-bottom: 2px solid #1b1f24; padding-bottom: 14px; margin-bottom: 26px; }
h1 { margin: 0 0 4px; font-size: 26px; }
h2 { font-size: 19px; margin: 34px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #e3e6ea; }
h3 { font-size: 15px; margin: 22px 0 8px; }
h4 { font-size: 13px; margin: 0 0 6px; color: #555c66; font-weight: 600; }
.sub { color: #555c66; font-size: 13px; }
.kpis { display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; }
.kpi { flex: 1 1 150px; border: 1px solid #e3e6ea; border-radius: 8px; padding: 10px 12px; background: #fafbfc; }
.kpi .label { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #6b737d; }
.kpi .value { font-size: 19px; font-weight: 600; margin-top: 2px; }
.kpi .note { font-size: 11px; color: #6b737d; }
table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid #eceff2; text-align: left; vertical-align: top; }
th { background: #f5f7f9; font-weight: 600; font-size: 12px; text-transform: uppercase;
     letter-spacing: .03em; color: #4a525c; }
td.r, th.r { text-align: right; white-space: nowrap; }
tbody tr:nth-child(even) { background: #fcfdfe; }
.pos { color: #1f7a45; }
.neg { color: #c03247; }
.ok { color: #1f7a45; font-weight: 600; }
.warn { color: #a86400; font-weight: 600; }
.muted { color: #6b737d; font-size: 12.5px; }
.chart { width: 100%; height: auto; border: 1px solid #eceff2; border-radius: 8px; background: #fff; }
.grid { stroke: #eceff2; stroke-width: 1; }
.axis { font-size: 10px; fill: #6b737d; }
.axis.r { text-anchor: end; }
.axis.end { text-anchor: end; }
.legend { margin: 6px 0 0; font-size: 12px; color: #4a525c; }
.key { margin-right: 14px; }
.key i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; }
.bars { margin: 6px 0; }
.bar-row { display: flex; align-items: center; gap: 8px; margin: 3px 0; font-size: 12.5px; }
.bar-label { flex: 0 0 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { flex: 1 1 auto; background: #f0f2f5; border-radius: 3px; height: 12px; }
.bar { display: block; height: 12px; border-radius: 3px; }
.bar.pos { background: #2e9e5b; }
.bar.neg { background: #d6455d; }
.bar-value { flex: 0 0 90px; text-align: right; white-space: nowrap; }
.alloc-grid { display: flex; flex-wrap: wrap; gap: 18px; }
.alloc { flex: 1 1 440px; }
footer { margin-top: 36px; padding-top: 14px; border-top: 1px solid #e3e6ea; color: #6b737d; font-size: 12px; }
@media print {
  body { padding: 0; font-size: 11.5px; }
  section { break-inside: avoid; }
  h2 { break-after: avoid; }
}
"""


def _render(d: dict, base: str, last_sync: str) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    sections = "".join(
        [
            _section_summary(d, base),
            _section_value(d, base),
            _section_holdings(d, base),
            _section_performance(d, base),
            _section_pnl(d, base),
            _section_income(d, base),
            _section_tax(d, base),
            _section_health(d, last_sync),
        ]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio report {generated}</title>
<style>{_CSS}</style>
</head><body><div class="wrap">
<header>
  <h1>Portfolio report</h1>
  <div class="sub">Generated {_esc(generated)} &middot; base currency {_esc(base)}
      &middot; last DEGIRO sync {_esc(last_sync)}</div>
</header>
{sections}
<footer>
  Built from an unofficial DEGIRO integration and Yahoo end-of-day prices. Figures are
  reconstructed, not an official statement, and the tax section is informational only &mdash;
  not tax advice. Verify against DEGIRO's own reports and the Belastingdienst.
</footer>
</div></body></html>
"""
