# DEGIRO Explorer

Pull your full DEGIRO history and explore it in an interactive dashboard.

DEGIRO has no official public API, so this uses the unofficial
[`degiro-connector`](https://github.com/Chavithra/degiro-connector) library. Because
DEGIRO does **not** expose a daily portfolio-value time series, the historical value
chart is *reconstructed*: positions are rebuilt from your transaction history and then
valued on each past date using historical market prices backfilled from Yahoo Finance
(`yfinance`).

> ⚠️ Unofficial integration. Use at your own risk; this is for personal, read-only
> analysis of your own account. Nothing here places orders.

## How it works

```
DEGIRO API ──fetch──▶ SQLite (raw)
                          │
   yfinance ──prices──────┤
                          ▼
                  reconstruct + analytics ──▶ SQLite (derived) ──▶ Streamlit dashboard
```

1. **`scripts/sync.py`** logs in, downloads transactions, cash movements and product
   metadata, backfills historical prices/FX, reconstructs a daily value series, and
   writes everything to a local SQLite database.
2. **`dashboard/app.py`** reads that SQLite database and renders the dashboard. The UI
   never talks to DEGIRO directly.

## Setup

Requires **Python ≥ 3.14** and [**uv**](https://docs.astral.sh/uv/) for dependency
management ([install uv](https://docs.astral.sh/uv/getting-started/installation/)).

```bash
# Create the virtualenv and install all dependencies (incl. dev tools) from uv.lock:
uv sync

# Optional: enable the pre-commit hooks (ruff, mypy on commit; pytest on push):
uv run pre-commit install
```

Run commands inside the environment with `uv run` (e.g. `uv run streamlit run dashboard/app.py`).

Create your credentials and portfolio-config files from the templates:

```bash
cp .env.example .env                       # Windows: copy .env.example .env
cp tickers.yml.example tickers.yml         # optional: Yahoo ticker overrides + benchmark
cp holdings_meta.yml.example holdings_meta.yml   # optional: per-holding classification
```

`tickers.yml` and `holdings_meta.yml` are gitignored so your own holdings stay private.

Fill in `.env`:
- `DEGIRO_USERNAME` / `DEGIRO_PASSWORD`
- `DEGIRO_TOTP_SECRET` — **only for authenticator-app (TOTP) 2FA.** This is the **setup
  key** (text secret) shown when you enable 2FA on DEGIRO, not the 6-digit code. If you
  only saved the QR code, re-run the 2FA setup to reveal the text key.
  **Leave this blank if your account uses in-app approval** (see below).
- `DEGIRO_INT_ACCOUNT` — optional; fetched automatically if blank.
- `DEGIRO_START_YEAR` — optional; earliest year to pull. Blank derives it from the
  earliest activity already stored, so syncs don't rescan years before the account existed.
- `DEGIRO_RISK_FREE_PCT` — optional (default `2.0`); the risk-free hurdle used for the
  Sharpe ratio on the Performance tab.

### Two-factor authentication

Both DEGIRO 2FA styles are handled automatically:

| Your 2FA | What to do |
|----------|------------|
| **Authenticator app (TOTP)** | Put the setup key in `DEGIRO_TOTP_SECRET`. |
| **In-app approval** — DEGIRO pushes a "tap Yes" prompt to your phone | Leave `DEGIRO_TOTP_SECRET` **blank**. Sync pauses and waits (~2 min) for you to approve in the DEGIRO mobile app. |

Every full sync needs a fresh approval, so run `sync.py --offline` while iterating — it
re-runs the derivations from stored data without logging in.

## Usage

```bash
# 1. Download + reconstruct everything into data/degiro.db
python scripts/sync.py

# 2. Launch the dashboard
streamlit run dashboard/app.py
```

Prefer `./run.sh` (or `.\run.ps1` on Windows), which syncs and then launches the
dashboard on port 8501 — or the next free port, if another app already has 8501.

`sync.py` is incremental-friendly — re-run it any time to pull new activity and refresh
the reconstruction.

### Convenience scripts

To avoid retyping the sequence, use the wrapper scripts which activate the venv, sync,
then launch the dashboard:

```powershell
# Windows PowerShell:
.\run.ps1              # full sync (logs in — needs phone approval), then dashboard
.\run.ps1 --offline    # re-run derivations from stored data (no login), then dashboard
.\run.ps1 --no-sync    # skip sync, just open the dashboard
```

```bash
# Git Bash / macOS / Linux:
./run.sh               # same flags as above
```

> **PowerShell execution policy:** if `.\run.ps1` is blocked with a "running scripts is
> disabled on this system" error, allow scripts for the current session only:
> ```powershell
> Set-ExecutionPolicy -Scope Process Bypass
> ```
> This resets when you close the window. To allow local scripts permanently, use
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` instead.

## Dashboard

Six tabs, all reading only from local SQLite:

| Tab | Contents |
|-----|----------|
| **Overview** | Total value over time, contributions vs market growth |
| **Performance** | Time-weighted return with a **benchmark overlay**, absolute P/L, drawdown, risk metrics, correlation matrix |
| **Holdings** | Per-holding values, returns, allocation breakdowns and TER |
| **Transactions & Income** | Trade ledger, dividends and fees, trailing yield, upcoming payments |
| **Tax (NL Box 3)** | Dutch Box 3 estimator + reconciliation against DEGIRO's official reports |
| **Data** | Health panel and CSV exports |

A **Glossary** expander defines every acronym (TWR, TER, peildatum, …).

**Benchmarks** are configured in `tickers.yml` under `benchmarks:` (default `IWDA.AS`)
and backfilled from Yahoo on every sync. **Per-holding classification and TER** live in
`holdings_meta.yml`, keyed by ISIN.

### Tax (NL Box 3)

> ℹ️ Informational only — not tax advice. Verify against the
> [Belastingdienst](https://www.belastingdienst.nl).

The Netherlands does not tax realised gains or actual dividends for private investors.
Box 3 instead taxes a *deemed* return on your asset value on **1 January** (the
*peildatum*), above a tax-free allowance. The tab estimates this and cross-checks every
figure against DEGIRO's own account statement and position report, at both portfolio and
per-holding level.

Yearly parameters live in `analytics.BOX3_PARAMS` and are picked with a year selector.
**Re-verify them each year** — Box 3 is mid-reform toward taxing actual returns, and
announced figures are sometimes revised before they are enacted:

| Year | Deemed return (investments) | Allowance (single) | Rate |
|------|------|------|------|
| 2024 | 6.04% | €57,000 | 36% |
| 2025 | 5.88% | €57,684 | 36% |
| 2026 | 6.00% | €59,357 | 36% |

### Fixing unresolved tickers

Yahoo tickers don't map cleanly from ISINs. If `sync.py` prints `unresolved ticker`
warnings, add the mappings to `tickers.yml` (ISIN or DEGIRO symbol → exact Yahoo ticker
with exchange suffix, e.g. `IE00B4L5Y983: IWDA.AS`) and re-run sync. Positions with
missing prices are flagged in the dashboard.

### Sanity check

At the end of sync, the reconstructed *current* value is compared to the live value
reported by DEGIRO. A small delta is expected (price-source differences, intraday
timing); a large delta usually means an unresolved ticker or FX issue.

## Project layout

| Path | Purpose |
|------|---------|
| `config.py` | Loads `.env` settings |
| `degiro_explorer/client.py` | Login via degiro-connector (TOTP *and* in-app 2FA) |
| `degiro_explorer/fetch.py` | Pull transactions, cash movements, products, portfolio, reports |
| `degiro_explorer/store.py` | SQLite schema + read/write helpers |
| `degiro_explorer/prices.py` | Ticker resolution + price/FX backfill (yfinance) |
| `degiro_explorer/reconstruct.py` | Daily positions → daily portfolio value |
| `degiro_explorer/analytics.py` | Returns, P/L, dividends, allocation, Box 3 params |
| `degiro_explorer/reports.py` | Parse DEGIRO's official CSVs + cross-check our figures |
| `scripts/sync.py` | Orchestrates the full pipeline |
| `scripts/freeport.py` | Picks the first free port (Streamlit aborts on a taken one) |
| `dashboard/app.py` | Streamlit dashboard |

## Development

Dependencies and tooling are managed with **uv** (`pyproject.toml` + `uv.lock`).

```bash
uv sync                      # install runtime + dev dependencies
uv run ruff check .          # lint
uv run ruff format .         # auto-format
uv run mypy .                # type-check
uv run pytest                # tests + coverage
uv run pytest -k foo --no-cov  # targeted run, skipping the coverage floor
```

`pytest` reports coverage and fails below a floor set in `pyproject.toml`. It is a
**ratchet against regressions**, not a quality claim — raise it as tests land.

The same checks run on every push/PR via GitHub Actions (`.github/workflows/ci.yml`) and,
optionally, locally through pre-commit hooks (`uv run pre-commit install`).

CI runs the full gate on **both Ubuntu and Windows** — Windows is the primary target and
carries platform-specific logic (port probing, console encoding) that a Linux-only build
would never exercise. A separate job runs [gitleaks](https://github.com/gitleaks/gitleaks-action)
over the full history, since `.gitignore` is otherwise the only thing keeping `.env` out
of the repo. [Dependabot](.github/dependabot.yml) opens weekly PRs for action and
dependency bumps so pins don't silently rot.
