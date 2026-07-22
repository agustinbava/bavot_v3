# Scalp Analyzer

Intraday scalping technical analysis. Given a watchlist of tickers (stocks/ETFs
and crypto), it downloads OHLCV candles on three timeframes (5m/15m/30m),
computes indicators, builds a compact JSON payload per instrument, sends it to
the Anthropic API with a scalping-analyst prompt, and prints a color-coded
summary table of verdicts (NO TRADE / LONG / SHORT).

> Technical analysis, not financial advice. Risk management is yours.

## Architecture

```
main.py            CLI entry / orchestration
config.py          loads config.yaml + env overrides
config.yaml        watchlist + parameters
fetchers/
  binance.py       crypto OHLCV (public API, no auth)
  ibkr.py          stocks/ETFs via ib_insync (needs TWS/Gateway)
indicators.py      Stoch RSI, volume ratio, levels, payload builder
analyzer.py        Anthropic call + verdict parsing
output.py          rich table + verbose panels
prompts/
  scalping_analyst.txt   editable system prompt (loaded at runtime)
runs/              archived payloads + responses per run (gitignored)
tests/             synthetic-data tests for indicators
```

## Setup

1. **Python 3.12+** (required by `pandas-ta` 0.4.x). Create a venv and install:

   ```bash
   cd scalp-analyzer
   python3.12 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

   > Use `.venv/bin/python` directly if your shell aliases `python`/`pip` to a
   > system interpreter. `pandas-ta` 0.4.x works with numpy 2 / pandas 2.2.

2. **Env vars.** Copy the example and fill in your key:

   ```bash
   cp .env.example .env
   # edit .env -> ANTHROPIC_API_KEY=sk-ant-...
   ```

   No API keys are hardcoded; everything sensitive comes from `.env`.

3. **IBKR (only for stock/ETF tickers).** Start TWS or IB Gateway and enable
   API access (Configure → API → Settings → *Enable ActiveX and Socket
   Clients*). Default ports: `7497` paper TWS, `7496` live TWS, `4002/4001`
   Gateway. Set the port in `config.yaml` (or `IBKR_PORT` in `.env`).
   Crypto (Binance) needs nothing running — you can test immediately.

## Usage

```bash
# Full watchlist
python main.py --watchlist config.yaml

# Just crypto, no API calls — builds payloads and prints one (works offline)
python main.py --only BTCUSDT,ETHUSDT --dry-run

# One ticker with the full model analysis printed
python main.py --only MELI --verbose

# Analyze up to 4 instruments concurrently
python main.py --parallel 4

# Override timeframes
python main.py --timeframes 5m,15m,30m
```

### Flags

| Flag | Meaning |
|------|---------|
| `--watchlist PATH` | Config YAML (default `config.yaml`) |
| `--only A,B,C` | Filter to these symbols |
| `--timeframes 5m,15m,30m` | Override configured timeframes |
| `--dry-run` | Build payloads, print one example, no API calls |
| `--parallel N` | Concurrent analyses via asyncio (default 1) |
| `--verbose` | Print each instrument's full analysis |

Every non-dry run is archived to `runs/YYYY-MM-DD_HHMM/` with, per ticker, the
exact `*.payload.json` sent and the `*.response.txt` returned — so you can audit
later what the model actually saw. Results are also stored in a local SQLite
database (`bavot.db`) that feeds the dashboard.

## Dashboard

A local HTML dashboard shows the day's analyses — date/time of each run, the
tickers analyzed, verdict/entry/SL/TP/R/R/confidence, and the full model
analysis behind an expandable row. Stat tiles summarize the day (longs, shorts,
no-trades, errors). Light/dark theme with a toggle; auto-refreshes every 60 s.

```bash
python dashboard.py            # → http://127.0.0.1:8787
python dashboard.py --port 9000
```

It reads from `bavot.db` (stdlib only, no extra dependencies). Filter by date
with the buttons at the top, or "Todo" for full history.

## Paper trading (signal evaluation)

Every LONG/SHORT verdict is registered as a virtual signal and later replayed
against real 1-minute candles by `evaluator.py` (run hourly via cron, or
manually):

```bash
python evaluator.py --verbose
```

Rules: touch-triggered entry within 4h (else `not_triggered`); first of TP/SL
touched decides; a 1m candle spanning both counts conservatively as SL
(`ambiguous`); open trades close at market after 8h (`expired`). Outcomes
(P&L %, R multiple, win rate, cumulative R) live in the `signals` table and
in the dashboard's "Señales" section.

## Configuration

Edit `config.yaml`: watchlist (`{symbol, type}` where type is `stock` or
`crypto`), timeframes, `num_candles`, `price_decimals`, IBKR connection,
Anthropic `model` / `max_tokens` / `prompt_path`, and indicator parameters
(Stoch RSI 3/3/14/14, volume SMA, swing lookback).

The system prompt lives in `prompts/scalping_analyst.txt` and is read at
runtime — edit it without touching code.

## Tests

```bash
pytest
```

`tests/test_indicators.py` exercises the indicator math on synthetic OHLCV data,
so it runs fully offline.

## Notes

- A failing ticker is logged and skipped; the batch continues. Fetch errors
  still appear in the summary table as `ERROR`.
- The model id defaults to `claude-sonnet-4-6` (configurable in `config.yaml`
  or via `ANTHROPIC_MODEL`). Point it at whichever current model you have
  access to.
- Verdict parsing is best-effort against the prompt's output format. If a
  response can't be parsed it's shown as `UNPARSED`; the raw text is always
  saved in `runs/` and shown with `--verbose`.
```
