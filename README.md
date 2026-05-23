# FX Signal Board

A personal automated FX trading dashboard built on free infrastructure. GitHub Actions runs two scanners on schedule, writes results to `data/signals.json`, and deploys the dashboard to GitHub Pages. Everything from data fetching to AI analysis to the frontend runs at zero hosting cost.

**Live dashboard:** `https://pieter800320.github.io/fx-signal-board/`

---

## What it does

Every hour, a scanner fetches H1 price data for 12 FX pairs, aggregates it into H4 and D1 timeframes, computes a full technical scoring suite, and saves the results. Every two hours, a second scanner fetches macro cross-asset data, ranks the best setups, and runs AI analysis via Claude Haiku. The dashboard reads from `signals.json` on GitHub and renders everything in a single mobile-first HTML file with no build step and no framework.

---

## Dashboard

### Sections

**WEEK AHEAD** — appears Sunday 21:00 UTC, persists until Monday 21:00 UTC. Claude searches the web for weekly FX previews and writes a 4-sentence strategic briefing: dominant macro theme, most important event, one pair to watch, biggest risk.

**Regime card** — D1 / H4 / H1 regime classification (Risk-On / Risk-Off / Ranging / Mixed) with confidence level and stability. The card border colour reflects the H4 regime. Tap the Macro card below it to see cross-asset detail.

**Macro card** — tap to flip between the W1 + D1 macro label view and a live 10-asset grid:

| Asset | Source | What it signals |
|---|---|---|
| VIX | Yahoo Finance | Fear/complacency regime |
| US 10Y | Yahoo Finance | Rate differential, flight-to-safety |
| US 3M | Yahoo Finance | Short-end Fed expectations |
| WTI Oil | Yahoo Finance | CAD strength driver |
| Gold | Yahoo Finance | Safe-haven demand |
| S&P 500 | Yahoo Finance | Risk appetite |
| Copper | Yahoo Finance | China/global growth proxy |
| DXY | Yahoo Finance | USD index |
| Bitcoin | Yahoo Finance | Risk appetite velocity |
| 10Y-3M spread | Computed | Yield curve — negative = inverted |

Each asset shows today's value, daily change (% or basis points), and a coloured direction arrow.

**CATALYST** — scans the last 6 hours of FX headlines every 2 hours and asks one adversarial question: does any breaking news conflict with or invalidate the current top setups? Max 25 words. The left accent bar turns grey when no catalysts are found.

**SETUPS** — deterministic Python scoring ranks all pairs. The top 3 are shown as pill badges (pair + direction arrow + score/10) with a one-sentence Haiku narrative explaining the macro context. Updated every 2 hours.

**CALENDAR** — high-impact FX events for the week, sourced via Claude web search every 6 hours. Shows forecast vs previous values and a brief interpretation per event. Pair blocks get a bright grey border when their currency has an event in the next 24 hours.

**CSM bar chart** — 8 major currencies ranked by strength on a 0–100 scale. Tap to cycle D1 / H4 / H1.

**Pair grid** — 12 pairs sorted by setup rank score. Each block:
- Top row: pair name + daily and 5-day % change
- **Default face:** D1/H4/H1 pill arrows + continuation score + correlated pairs
- **Tapped face:** MOM1212 scores and deltas + CMP + ADX
- Bottom: currency strength bar (base vs quote)
- Grey border: high-impact event coming within 24 hours

**Chart overlay** — long-press any pair block. Full-screen LightweightCharts with EMA200, EMA50, Bollinger Bands, MACD. Draw price levels with direction-based Telegram alerts. GitHub sync keeps levels across devices.

**ⓘ button** — tap to open a full plain-language guide explaining every section, score, and calculation in the dashboard.

---

## Scoring system

### Continuation score (0–100%)

Measures multi-timeframe technical alignment. Five components:

| Component | Weight | Description |
|---|---|---|
| TF alignment | 35% | D1 / H4 / H1 pills all pointing the same direction |
| Entry position | 23% | Reset score (pullback quality) + ATR percentile (volatility position) |
| CSM divergence | 16% | H4 currency strength gap between base and quote |
| Regime fit | 13% | Does pair direction match the H4 regime? |
| ADX weight | 13% | Trend strength multiplier |

### MOM1212 oscillator (0–100)

Proprietary momentum oscillator computed independently for D1, H4, and H1. Uses a tanh-normalised composite of EMA crossovers, RSI, and price momentum. Above 60 = bullish, below 40 = bearish, 40–60 = neutral zone.

**CMP** (Composite Momentum Position) — single 0–100 number combining all three timeframes. The primary momentum reading. Above 60 = bullish, below 40 = bearish.

### Setup rank score (0–10)

Six-component deterministic scorer. Hard gate: D1 pill must be directional AND continuation ≥ 45%.

| Component | Weight | Logic |
|---|---|---|
| Continuation score | 25% | Linear scale 45–100% → 0–10 |
| CMP | 20% | ≥60 bull / ≤40 bear = 10. Neutral zone (40–60) = 0 |
| D1 MOM delta | 15% | Direction and magnitude of momentum acceleration |
| CSM divergence | 20% | D1 strength gap, base vs quote |
| Regime fit | 10% | D1 regime × confidence multiplier |
| Cross-asset | 10% | Currency impact from 16 asset/direction combinations |

The cross-asset component maps macro signals to FX currencies — e.g. rising WTI → CAD strength, rising VIX → JPY/CHF strength, rising DXY → USD strength.

### Regime classification

Three independent signals vote per timeframe: (1) CSM divergence between safe-haven and risk-on currencies, (2) USD positioning vs all others, (3) directional balance of pair pills. Fewer than 40% directional pills overrides to Ranging. Risk-On and Risk-Off scores are computed symmetrically on a 0–10 scale.

---

## Data pipeline

### Hourly scan (`scan_h1.py`)

```
Fetch H1 OHLCV — 12 pairs + 6 CSM crosses (Twelvedata, 5000 bars each)
Aggregate H1 → H4, D1
Compute pills (EMA200/50 + MACD + DMI + ADX)
Compute MOM1212 oscillator (D1/H4/H1 + deltas + CMP)
Compute CSM — 8 currencies, D1/H4/H1 blend
Extract ADX, reset score, ATR percentile
Compute correlation matrix
Compute D1/H4/H1 regimes
Compute continuation scores and assemble output
Write signals.json (preserving all scan_news keys)
Check EMA touch + price level alerts → Telegram
```

**Rate limiting:** targets 6 calls/min, leaving 2/min buffer for browser chart fetches. Detects daily credit exhaustion after 3 consecutive 429 responses and aborts immediately. Data quality guard: skips the write if fewer than 6/12 pairs were fetched, preserving the last good dataset.

### News scan (`scan_news.py`)

Runs every 2 hours. Sunday 21:00 UTC also triggers a Week Ahead scan.

```
Fetch 9 macro instruments via Yahoo Finance
Compute W1 regime and D1 macro momentum
Build macro_assets (values, deltas, direction, yield curve spread)
Fetch FX headlines via RSS
Refresh calendar via Claude web search if cache > 6 hours old
Rank pairs (deterministic Python — rank.py)
Haiku: one-sentence narrative per top setup
Haiku: catalyst check — adversarial, 25 words max
[Sunday 21:00 only] Haiku + web search: Week Ahead briefing
Write signals.json
```

---

## signals.json schema

**Written by `scan_h1.py` (hourly):**

```
updated          ISO timestamp
regime_d1/h4/h1  {regime, confidence, score, stable}
csm              {d1, h4, h1} → {CCY: 0-100 score}
correlations     {pairs, matrix}
pairs            {EURUSD, GBPUSD, ...} — see below
```

Per-pair:
```json
{
  "pills":       {"d1": "bear", "h4": "neutral", "h1": "bear"},
  "mom":         {"d1": 24, "dd1": -25, "h4": 49, "dh4": 44, "h1": 39, "dh1": -10, "cmp": 34},
  "adx":         23.3,
  "d1_pct":      -0.02,
  "d5_pct":      -0.43,
  "prev_close":  1.16042,
  "prev5_close": 1.16528,
  "cont":        61
}
```

**Written by `scan_news.py` (every 2h), preserved by `scan_h1.py`:**

```
regime_w1        {regime, confidence, score, stable}
macro            {label, confidence, stable, signals, total}
macro_assets     {vix, us10y, us3m, wti, gold, spx, copper, dxy, btc, curve}
catalyst         {text, updated}
ranked           {text, top: [{pair, direction, score}], updated}
calendar         {events: [{day, time, iso, currency, name, forecast, previous, note}], updated}
week_ahead       {text, generated_at}  — present Sunday–Monday only
last_alert       last Telegram alert timestamp
```

---

## Setup

### 1. Fork or clone

```bash
git clone https://github.com/Pieter800320/fx-signal-board
```

### 2. GitHub Secrets

Settings → Secrets and variables → Actions:

| Secret | Required | Description |
|---|---|---|
| `TWELVEDATA_KEY` | Yes | Free key from [twelvedata.com](https://twelvedata.com) — 800 calls/day |
| `ANTHROPIC_API_KEY` | Yes | Key from [console.anthropic.com](https://console.anthropic.com) — ~$2.40/month |
| `TELEGRAM_BOT_TOKEN` | Optional | From [@BotFather](https://t.me/BotFather) — alerts skipped if not set |
| `TELEGRAM_CHAT_ID` | Optional | Your Telegram chat ID |

### 3. Enable GitHub Pages

Settings → Pages → Source: Deploy from a branch → `main` → `/dashboard`

### 4. Add your Twelvedata key to the dashboard

Open the dashboard, tap ⚙, paste your Twelvedata API key. Stored in browser localStorage only — used for chart fetches from your browser, never sent to GitHub Actions.

### 5. GitHub PAT (optional)

To sync price level alerts across devices, tap ⚙ and paste a GitHub PAT with `repo` scope. Levels are saved to `data/level_alerts.json`.

---

## Schedules

| Workflow | Cron | What it does |
|---|---|---|
| `scan_h1.yml` | `5 * * * *` (hourly at :05) | OHLCV fetch + full scoring + Pages deploy |
| `scan_news.yml` | `0 */2 * * *` (every 2 hours) | Macro + AI analysis |
| `scan_news.yml` | `0 21 * * 0` (Sunday 21:00 UTC) | Week Ahead briefing |

---

## API usage

| Service | Free limit | Daily usage | Cost |
|---|---|---|---|
| Twelvedata OHLCV | 800 calls/day, 8/min | ~312 calls | Free |
| Twelvedata calendar | Not available on free tier | — | — |
| Yahoo Finance | Unofficial, no limit | 108 fetches | Free |
| Anthropic Haiku | 50 RPM | ~36 calls | ~$2.40/month |
| GitHub Actions | Unlimited (public repo) | ~3,240 min/month | Free |

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Vanilla JS, HTML, CSS — no framework, no build step |
| Charts | [LightweightCharts](https://tradingview.github.io/lightweight-charts/) v4.2 |
| Scanner | Python 3.11, pandas, numpy |
| Scheduling | GitHub Actions |
| Hosting | GitHub Pages |
| OHLCV data | [Twelvedata](https://twelvedata.com) free tier |
| Macro data | Yahoo Finance (unofficial) |
| Calendar data | Claude Haiku web search |
| AI analysis | Claude Haiku 4.5 via [Anthropic API](https://console.anthropic.com) |
| Alerts | Telegram Bot API |

---

## Project structure

```
fx-signal-board/
├── dashboard/
│   └── index.html              — full frontend, all CSS and JS inline
├── scanner/
│   ├── config.py               — pairs, currencies, timeframes, correlates
│   ├── fetch.py                — Twelvedata OHLCV fetch + dynamic rate limiter
│   ├── aggregator.py           — H1 → H4, D1 aggregation
│   ├── score.py                — EMA200/RSI/ADX/ATR scoring primitives
│   ├── pills.py                — bull/bear/neutral pill classification
│   ├── mom1212.py              — MOM1212 oscillator
│   ├── csm.py                  — currency strength model
│   ├── regime.py               — regime classification
│   ├── cont_score.py           — continuation score 0–100
│   ├── rank.py                 — setup rank scorer 0–10
│   ├── correlate.py            — correlation matrix
│   ├── structure.py            — BOS/CHOCH structure detection
│   ├── level_ema_alerts.py     — Telegram EMA touch + level alerts
│   ├── scan_h1.py              — hourly master scanner
│   └── scan_news.py            — 2h macro + AI scanner
├── data/
│   ├── signals.json            — live data (written by scanners)
│   └── level_alerts.json       — user-drawn price levels
├── .github/workflows/
│   ├── scan_h1.yml             — hourly scan + Pages deploy
│   └── scan_news.yml           — 2h news scan + Sunday Week Ahead
└── requirements.txt
```

---

## Disclaimer

Personal research tool. Nothing displayed constitutes financial advice. FX trading carries significant risk of loss.
