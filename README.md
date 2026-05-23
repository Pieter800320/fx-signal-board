# FX Signal Board

A personal automated FX trading dashboard. Runs entirely on free infrastructure — GitHub Actions for scheduling, GitHub Pages for the frontend, Twelvedata free tier for OHLCV data, Yahoo Finance for macro data, and the Anthropic API for AI analysis.

**Live dashboard:** `https://pieter800320.github.io/fx-signal-board/`

---

## What it does

Every hour, a GitHub Actions workflow fetches H1 OHLCV data for 12 FX pairs, aggregates it into H4 and D1 timeframes, computes a full technical scoring suite, and writes the results to `data/signals.json`. Every two hours, a second workflow fetches macro cross-asset data, runs AI analysis via Claude Haiku, and ranks the best current setups. The dashboard reads `signals.json` directly from the GitHub API and renders everything in a single-file mobile-first HTML page.

---

## Dashboard overview

The dashboard is a single HTML file with no build step, no framework, and no backend. It loads `signals.json` from the GitHub API on open and refreshes every 5 minutes.

### Sections (top to bottom)

**WEEK AHEAD** — appears Sunday 21:00 UTC, persists until Monday 21:00 UTC. Claude Haiku searches the web for current FX weekly previews and synthesises a 4-sentence strategic briefing incorporating live signals data.

**Regime card** — D1 / H4 / H1 regime classification (Risk-On / Risk-Off / Ranging / Mixed) with confidence and stability. A ⚡ Confluence badge appears when all three timeframes agree.

**Macro card** — tap to flip between W1 + D1 macro label, and a 10-asset cross-asset grid:

| Asset | Symbol | Signal |
|---|---|---|
| VIX | ^VIX | Fear/complacency regime |
| US 10Y | ^TNX | Rate differential driver |
| US 3M | ^IRX | Short-end Fed expectations |
| WTI Oil | CL=F | CAD driver |
| Gold | GC=F | Safe-haven demand |
| S&P 500 | ^GSPC | Risk appetite |
| Copper | HG=F | China growth proxy |
| DXY | DX-Y.NYB | USD index |
| Bitcoin | BTC-USD | Risk appetite velocity |
| 10Y-3M | computed | Yield curve spread (recession signal) |

Each asset shows current value, daily delta (% or bp), and a coloured direction arrow.

**CATALYST** — Claude Haiku reads the last 6 hours of FX headlines and answers one adversarial question: does any breaking news conflict with, accelerate, or invalidate the current top setups? Maximum 25 words. Red accent line turns grey when no breaking catalysts are found.

**SETUPS** — deterministic Python ranking of the best current setups, with pill badges (pair + direction + score) and a one-sentence Haiku narrative per setup.

**CALENDAR** — high-impact FX events for the week from a web search, with forecast vs previous and a brief Haiku interpretation note per event. Refreshed every 6 hours.

**CSM bar chart** — currency strength across 8 major currencies. Tap to cycle D1 / H4 / H1.

**Pair grid** — 12 pairs sorted by setup rank score (falling back to continuation score for unranked pairs). Each block shows:
- Daily and 5-day % change
- Tap face 0: D1/H4/H1 pill states + continuation score + correlated pairs
- Tap face 1: MOM1212 oscillator values and deltas + CMP + ADX
- Currency strength bar (base vs quote)
- Bright grey border when either currency has a high-impact event in the next 24 hours

**Chart overlay** — long-press any pair block to open a full-screen LightweightCharts overlay with EMA200, EMA50, Bollinger Bands, and MACD. Supports level drawing with direction alerts and GitHub sync.

---

## Scoring system

### Continuation score (0–100%)

Measures multi-timeframe technical alignment. Five components:

1. **TF alignment (35%)** — D1/H4/H1 pills all pointing the same direction
2. **Entry position (23%)** — reset score (pullback quality) + ATR percentile (volatility position)
3. **CSM divergence (16%)** — H4 currency strength gap between base and quote
4. **Regime fit (13%)** — does the pair direction match the H4 regime?
5. **ADX weight (13%)** — trend strength multiplier

### MOM1212 oscillator (0–100)

Proprietary momentum oscillator computed independently for D1, H4, and H1 timeframes. Uses a `tanh`-normalised composite of EMA crossovers, RSI, and price momentum. Values above 60 = bullish bias, below 40 = bearish bias. The CMP (Composite Momentum Position) aggregates all three timeframes into a single 0–100 score.

### Setup rank score (0–10)

Deterministic 6-component scorer in `scanner/rank.py`. Hard gate: D1 pill must be directional and continuation score ≥ 45.

| Component | Weight | Description |
|---|---|---|
| Continuation score | 25% | Linear scale 45–100 → 0–10 |
| CMP | 20% | ≥60 bull / ≤40 bear = 10, noise zone = 0 |
| D1 MOM delta | 15% | Direction and magnitude of momentum acceleration |
| CSM divergence | 20% | D1 base vs quote currency strength gap |
| Regime fit | 10% | D1 regime × confidence multiplier |
| Cross-asset | 10% | Currency impact from macro asset moves |

The cross-asset component maps 16 asset/direction combinations to FX currency impacts (e.g. rising WTI → CAD strength, rising VIX → JPY/CHF strength).

### Regime classification

Three independent votes (CSM divergence, USD positioning, pill directional balance) determine D1/H4/H1 regime. A "Ranging" override fires when fewer than 40% of pills are directional. Risk-On and Risk-Off scores are computed symmetrically on a 0–10 scale.

---

## Data pipeline

### Hourly scan (`scan_h1.py`)

```
Fetch H1 OHLCV (12 pairs + 6 CSM crosses via Twelvedata)
  ↓
Aggregate H1 → H4, D1
  ↓
Compute pills (EMA200/50 + MACD + DMI + ADX)
  ↓
Compute MOM1212 (D1/H4/H1 + deltas + CMP)
  ↓
Compute CSM (D1 + H4 + H1 blend, 8 currencies)
  ↓
Compute ADX, reset score, ATR percentile
  ↓
Compute correlation matrix
  ↓
Compute D1/H4/H1 regimes
  ↓
Compute continuation scores + assemble pairs
  ↓
Write signals.json (preserving scan_news keys)
  ↓
Check EMA touch + level alerts → Telegram
```

**Rate limiting:** targets 6 calls/min (leaving 2/min for browser chart fetches). Detects daily credit exhaustion after 3 consecutive 429s and aborts cleanly. Data quality guard: if fewer than 6 of 12 pairs are fetched, the write is skipped to preserve the last good dataset.

### News scan (`scan_news.py`)

Runs every 2 hours. Sunday 21:00 UTC also triggers a Week Ahead scan.

```
Fetch 9 macro instruments via Yahoo Finance
  ↓
Compute W1 regime + D1 macro momentum
  ↓
Build macro_assets (values, deltas, direction, yield curve)
  ↓
Fetch FX headlines (RSS)
  ↓
Refresh calendar events every 6h via Haiku + web search
  ↓
Rank pairs (deterministic Python)
  ↓
Haiku: ranked setup narrative (1 sentence per setup)
  ↓
Haiku: catalyst check (adversarial, 25 words max)
  ↓
[Sunday 21:00] Haiku + web search: Week Ahead briefing
  ↓
Write signals.json
```

---

## `signals.json` structure

All keys written by `scan_h1.py` (hourly):

```
updated          ISO timestamp of last hourly scan
regime_d1        {regime, confidence, score, stable}
regime_h4        {regime, confidence, score, stable}
regime_h1        {regime, confidence, score, stable}
csm              {d1: {CCY: 0-100, ...}, h4: {...}, h1: {...}}
correlations     {pairs: [...], matrix: [[...]]}
pairs            {EURUSD: {...}, ...}  ← see below
```

Per-pair structure:
```json
{
  "pills":       {"d1": "bear", "h4": "neutral", "h1": "bear"},
  "mom":         {"d1": 24, "dd1": -25, "h4": 49, "dh4": 44,
                  "h1": 39, "dh1": -10, "cmp": 34},
  "adx":         23.3,
  "d1_pct":      -0.02,
  "d5_pct":      -0.43,
  "prev_close":  1.16042,
  "prev5_close": 1.16528,
  "cont":        61
}
```

All keys written by `scan_news.py` (every 2h), preserved by `scan_h1.py`:

```
regime_w1        {regime, confidence, score, stable}
macro            {label, confidence, stable, signals, total}
macro_assets     {vix, us10y, us3m, wti, gold, spx, copper, dxy, btc, curve}
catalyst         {text, updated}
ranked           {text, top: [{pair, direction, score}], updated}
calendar         {events: [{day, time, iso, currency, name, forecast, previous, note}], updated}
week_ahead       {text, generated_at}  ← present Sunday–Monday only
last_alert       last Telegram alert sent
```

---

## Setup

### 1. Fork or clone

```bash
git clone https://github.com/Pieter800320/fx-signal-board
```

### 2. GitHub Secrets

Add these secrets under **Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `TWELVEDATA_KEY` | Free API key from [twelvedata.com](https://twelvedata.com) |
| `ANTHROPIC_API_KEY` | API key from [console.anthropic.com](https://console.anthropic.com) |
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

Telegram secrets are optional — alerts are skipped if not set.

### 3. Enable GitHub Pages

**Settings → Pages → Source:** Deploy from a branch → `main` → `/dashboard`

### 4. Dashboard API key

Open the dashboard, tap ⚙, paste your Twelvedata API key. This is stored in `localStorage` and used only for chart data fetches directly from your browser — it never goes through GitHub Actions.

### 5. GitHub PAT (optional, for level sync)

To sync price level alerts across devices, tap ⚙ and paste a GitHub Personal Access Token with `repo` scope. Levels are saved to `data/level_alerts.json` in the repo.

---

## Schedules

| Workflow | Schedule | Description |
|---|---|---|
| `scan_h1.yml` | Every hour at :05 | OHLCV fetch + full technical scoring + deploy |
| `scan_news.yml` | Every 2 hours | Macro + AI analysis |
| `scan_news.yml` | Sunday 21:00 UTC | Week Ahead briefing (web search) |

---

## API usage (free tier)

| Service | Free limit | Daily usage | Headroom |
|---|---|---|---|
| Twelvedata | 800 calls/day, 8/min | ~312/day | 488 spare |
| Anthropic Haiku | 50 RPM | ~36 calls/day | Negligible |
| Yahoo Finance | Unofficial | 108 fetches/day | No formal limit |
| GitHub Actions | Unlimited (public repo) | ~3,240 min/month | Unlimited |

Approximate Anthropic cost: **~$2.40/month**.

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Vanilla JS + HTML, LightweightCharts v4.2 |
| Charts | [Lightweight Charts](https://tradingview.github.io/lightweight-charts/) |
| Backend | Python 3.11, pandas, numpy |
| Scheduling | GitHub Actions |
| Hosting | GitHub Pages |
| OHLCV data | [Twelvedata](https://twelvedata.com) free tier |
| Macro data | Yahoo Finance (unofficial) |
| AI analysis | Claude Haiku 4.5 via [Anthropic API](https://console.anthropic.com) |
| Alerts | Telegram Bot API |

---

## Project structure

```
fx-signal-board/
├── dashboard/
│   └── index.html          # Single-file frontend (all CSS + JS inline)
├── scanner/
│   ├── config.py            # Pairs, currencies, timeframes, correlates
│   ├── fetch.py             # Twelvedata OHLCV fetch with rate limiter
│   ├── aggregator.py        # H1 → H4, D1 aggregation
│   ├── score.py             # EMA200/RSI/ADX/ATR scoring primitives
│   ├── pills.py             # Bull/bear/neutral pill classification
│   ├── mom1212.py           # MOM1212 oscillator
│   ├── csm.py               # Currency strength model
│   ├── regime.py            # Regime classification
│   ├── cont_score.py        # Continuation score 0–100
│   ├── rank.py              # Setup rank scorer 0–10
│   ├── correlate.py         # Correlation matrix
│   ├── structure.py         # BOS/CHOCH structure detection
│   ├── level_ema_alerts.py  # Telegram EMA touch + level alerts
│   ├── scan_h1.py           # Hourly master runner
│   └── scan_news.py         # 2h macro + AI runner
├── data/
│   ├── signals.json         # Live data (written by scanners)
│   └── level_alerts.json    # User-drawn price levels
├── .github/workflows/
│   ├── scan_h1.yml          # Hourly scan + Pages deploy
│   └── scan_news.yml        # 2h news + Sunday Week Ahead
└── requirements.txt
```

---

## Disclaimer

This tool is for personal research and educational purposes only. Nothing displayed constitutes financial advice. FX trading involves significant risk of loss.
