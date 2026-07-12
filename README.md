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
- `DEGIRO_TOTP_SECRET` — only if 2FA is enabled. This is the **setup key** (text secret)
  shown when you enable 2FA on DEGIRO, not the 6-digit code. If you only saved the QR
  code, re-run the 2FA setup to reveal the text key.
- `DEGIRO_INT_ACCOUNT` — optional; fetched automatically if blank.

## Usage

```bash
# 1. Download + reconstruct everything into data/degiro.db
python scripts/sync.py

# 2. Launch the dashboard
streamlit run dashboard/app.py
```

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
| `degiro_explorer/client.py` | Login via degiro-connector (TOTP) |
| `degiro_explorer/fetch.py` | Pull transactions, cash movements, products, portfolio |
| `degiro_explorer/store.py` | SQLite schema + read/write helpers |
| `degiro_explorer/prices.py` | Ticker resolution + price/FX backfill (yfinance) |
| `degiro_explorer/reconstruct.py` | Daily positions → daily portfolio value |
| `degiro_explorer/analytics.py` | Returns, P/L, dividends, allocation |
| `scripts/sync.py` | Orchestrates the full pipeline |
| `dashboard/app.py` | Streamlit dashboard |

## Development

Dependencies and tooling are managed with **uv** (`pyproject.toml` + `uv.lock`).

```bash
uv sync                      # install runtime + dev dependencies
uv run ruff check .          # lint
uv run ruff format .         # auto-format
uv run mypy .                # type-check
uv run pytest                # tests
```

The same checks run on every push/PR via GitHub Actions (`.github/workflows/ci.yml`) and,
optionally, locally through pre-commit hooks (`uv run pre-commit install`).
