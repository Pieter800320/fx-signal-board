"""
FX Signal Board — hourly master scanner

Replaces scan_full.py + scan_hourly.py.

Flow:
  1. Fetch H1 OHLCV for 12 main pairs + 6 CSM cross pairs  (18 API calls)
  2. Aggregate H1 -> H4 + D1 via aggregator.py
  3. Pills (full Forex1212 formula) via pills.py / score.py
  4. MOM1212 (D1/H4/H1 + deltas + CMP) via mom1212.py
  5. CSM (D1 + H4 blend, 16-pair set) via csm.py
  6. ADX (H4)
  7. Correlation matrix via correlate.py
  8. H4 Regime via regime.py
  9. Cont. score (computeQAI port) via cont_score.py
 10. D1% / D5% / prev_close / prev5_close
 11. Preserve news/macro/analysis from previous signals.json
 12. Write signals.json
 13. Gold signal computation (gold_signal key)
 14. Telegram: Gold + H4 + H1 confirmed
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scanner.config     import PAIRS, TF_INTERVAL, TF_BARS
from scanner.fetch      import fetch_ohlcv
from scanner.aggregator import build_tfs
from scanner.pills      import classify_full
from scanner.mom1212    import compute_all as compute_mom
from scanner.csm        import compute_csm, STRENGTH_PAIRS
from scanner.regime     import classify_regime
from scanner.cont_score import compute_cont
from scanner.correlate  import compute_correlation
from scanner.score              import compute_reset_score, atr_percentile
from scanner.level_ema_alerts   import check_levels, check_ema_touches

# Extra pairs needed for CSM 16-pair set (not in main PAIRS list)
CSM_EXTRA = ["EUR/GBP", "EUR/CHF", "GBP/CHF", "AUD/NZD", "AUD/CAD", "GBP/AUD"]

SCAN_TF = "h1"  # primary fetch timeframe


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_signals():
    path = ROOT / "data" / "signals.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_signals(data: dict):
    path = ROOT / "data" / "signals.json"
    path.parent.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def send_telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat  = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
    if not token or not chat:
        print("  Telegram: BOT_TOKEN or CHAT_ID not set in secrets")
        return
    print(f"  Telegram: sending to chat_id={chat!r} (token length={len(token)})")
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id":    chat,
        "text":       msg,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            print(f"  Telegram: {r.status} — {body[:200]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  Telegram error {e.code}: {body}")
    except Exception as e:
        print(f"  Telegram error: {e}")


def regime_emoji(regime: str) -> str:
    return {"Risk-Off": "🔴", "Risk-On": "🟢", "Mixed": "🟡", "Ranging": "⚪"}.get(regime, "")


def d_pct(df, bars_back: int):
    """Return % change over bars_back bars on D1 aggregated data."""
    if df is None or len(df) < bars_back + 1:
        return None
    prev  = float(df["close"].iloc[-(bars_back + 1)])
    close = float(df["close"].iloc[-1])
    if prev == 0:
        return None
    return round((close / prev - 1) * 100, 2)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== FX Signal Board — Hourly Scan ===")
    now = datetime.now(timezone.utc)

    prev             = load_signals()
    prev_d1_regime   = prev.get("regime_d1")
    prev_h4_regime   = prev.get("regime_h4")
    prev_h1_regime   = prev.get("regime_h1")
    prev_regime_name = (prev_h4_regime or {}).get("regime", "Unknown")

    # ── 1. Fetch H1 OHLCV ────────────────────────────────────────────────────
    all_pairs  = PAIRS + [p for p in CSM_EXTRA if p not in PAIRS]
    total_fetches = len(all_pairs)
    print(f"\n[1/9] Fetching H1 OHLCV for {total_fetches} pairs "
          f"({TF_BARS['h1']} bars each)…")

    raw_ohlcv = {}   # { "EURUSD": h1_df }
    for i, pair in enumerate(all_pairs):
        key = pair.replace("/", "")
        print(f"  [{i+1}/{total_fetches}] {key} H1")
        try:
            df = fetch_ohlcv(pair, TF_INTERVAL["h1"], TF_BARS["h1"])
            if df is not None:
                raw_ohlcv[key] = df
        except RuntimeError as e:
            # Daily credit limit — abort fetch loop immediately
            print(f"  ✗ {e}")
            print(f"  Aborting fetch — {len(raw_ohlcv)}/{total_fetches} pairs retrieved.")
            break
        except Exception as e:
            print(f"  ⚠ {key} skipped: {e}")
        # No manual sleep — fetch.py _rate_wait() handles spacing dynamically

    # Guard: fewer than half the main pairs = don't overwrite good data
    main_fetched = sum(1 for p in PAIRS if p.replace("/", "") in raw_ohlcv)
    if main_fetched < len(PAIRS) // 2:
        print(f"\n✗ Only {main_fetched}/{len(PAIRS)} main pairs fetched — "
              f"keeping existing signals.json to avoid overwriting good data.")
        return

    # ── 2. Aggregate H1 -> H4 + D1 ────────────────────────────────────────────
    print("\n[2/9] Aggregating H1 -> H4 + D1…")
    ohlcv = {}   # { "EURUSD": {"h1": df, "h4": df, "d1": df} }
    for key, h1_df in raw_ohlcv.items():
        tfs = build_tfs(h1_df)
        ohlcv[key] = tfs
        print(f"  {key}: H1={len(tfs['h1'])} H4={len(tfs['h4'])} D1={len(tfs['d1'])}")

    # ── 3. Pills (full Forex1212 formula) ─────────────────────────────────────
    print("\n[3/9] Computing pills (EMA200/50 + MACD + DMI + ADX weight)…")
    pair_pills  = {}   # { "EURUSD": {"d1": "bear", ...} }
    pair_scores = {}   # { "EURUSD": {"d1": result, "h4": result, "h1": result} }

    for key in ohlcv:
        tfs    = {tf: ohlcv[key][tf] for tf in ("d1", "h4", "h1")}
        result = classify_full(tfs)
        pair_pills[key]  = result["pills"]
        pair_scores[key] = result["scores"]
        print(f"  {key}: {result['pills']}")

    # ── 4. MOM1212 ────────────────────────────────────────────────────────────
    print("\n[4/9] Computing MOM1212 (D1/H4/H1 + deltas + CMP)…")
    pair_mom = {}
    for key in [p.replace("/", "") for p in PAIRS]:
        tfs = {tf: ohlcv.get(key, {}).get(tf) for tf in ("d1", "h4", "h1")}
        pair_mom[key] = compute_mom(tfs)
        print(f"  {key}: CMP={pair_mom[key].get('cmp')}")

    # ── 5. CSM ────────────────────────────────────────────────────────────────
    print("\n[5/9] Computing CSM (16-pair D1+H4 blend)…")
    csm = compute_csm(ohlcv)
    print(f"  D1: {dict(sorted(csm['d1'].items(), key=lambda x: -x[1]))}")
    print(f"  H4: {dict(sorted(csm['h4'].items(), key=lambda x: -x[1]))}")
    print(f"  H1: {dict(sorted(csm['h1'].items(), key=lambda x: -x[1]))}")

    # ── 6. ADX + per-pair entry metrics ───────────────────────────────────────
    print("\n[6/9] Extracting ADX, reset_score, atr_percentile…")
    pair_adx        = {}
    pair_reset      = {}
    pair_atr_pct    = {}

    for key in [p.replace("/", "") for p in PAIRS]:
        sc = pair_scores.get(key, {})

        # ADX: from H4 score raw indicators (already computed in score_pair)
        h4_score = sc.get("h4")
        adx_val  = (h4_score["raw"]["adx"] if h4_score and h4_score.get("raw") else None)
        pair_adx[key] = adx_val

        # Reset score: computed from H4 closes, direction from D1 pill
        d1_dir   = pair_pills.get(key, {}).get("d1", "neutral")
        h4_df    = ohlcv.get(key, {}).get("h4")
        if h4_df is not None and len(h4_df) >= 34:
            pair_reset[key] = compute_reset_score(
                h4_df["close"].values, direction=d1_dir
            )
        else:
            pair_reset[key] = None

        # ATR percentile: from D1 (or H4 as proxy if D1 is short)
        d1_df = ohlcv.get(key, {}).get("d1")
        h4_df = ohlcv.get(key, {}).get("h4")
        ap = atr_percentile(d1_df) if d1_df is not None and len(d1_df) >= 52 else None
        if ap is None and h4_df is not None and len(h4_df) >= 52:
            ap = atr_percentile(h4_df)
        pair_atr_pct[key] = ap

        print(f"  {key}: ADX={adx_val} reset={pair_reset[key]} atr_pct={ap}")

    # ── 7. Correlation matrix ─────────────────────────────────────────────────
    print("\n[7/9] Computing correlation matrix…")
    correlations = compute_correlation(ohlcv)

    # ── 8. D1 / H4 / H1 Regime ───────────────────────────────────────────────
    print("\n[8/9] Computing D1 / H4 / H1 regimes…")
    regime_d1 = classify_regime(csm["d1"], pair_pills, prev_d1_regime, tf="d1")
    regime_h4 = classify_regime(csm["h4"], pair_pills, prev_h4_regime, tf="h4")
    regime_h1 = classify_regime(csm["h1"], pair_pills, prev_h1_regime, tf="h1")
    new_regime_name = regime_h4["regime"]

    # Confluence: all three agree on the same non-Mixed/non-Ranging regime
    aligned_regime = None
    if (regime_d1["regime"] == regime_h4["regime"] == regime_h1["regime"]
            and regime_h4["regime"] not in ("Mixed", "Ranging")):
        aligned_regime = regime_h4["regime"]

    print(f"  D1: {regime_d1['regime']} {regime_d1['confidence']}")
    print(f"  H4: {regime_h4['regime']} {regime_h4['confidence']} "
          f"(was: {prev_regime_name})")
    print(f"  H1: {regime_h1['regime']} {regime_h1['confidence']}")
    if aligned_regime:
        print(f"  ⚡ CONFLUENCE: all 3 TFs → {aligned_regime}")

    # ── 9. Cont. score + assemble pairs ───────────────────────────────────────
    print("\n[9/9] Computing cont. scores + assembling pairs…")
    pairs_out = {}

    for pair in PAIRS:
        key   = pair.replace("/", "")
        pills = pair_pills.get(key, {})
        mom   = pair_mom.get(key, {})
        adx   = pair_adx.get(key)

        cont = compute_cont(
            pair        = key,
            pills       = pills,
            adx         = adx,
            csm_h4      = csm["h4"],
            regime_h4   = regime_h4,
            reset_score = pair_reset.get(key),
            atr_pct     = pair_atr_pct.get(key),
        )

        d1_df       = ohlcv.get(key, {}).get("d1")
        d1p         = d_pct(d1_df, 1)
        d5p         = d_pct(d1_df, 5)
        prev_close  = (round(float(d1_df["close"].iloc[-2]), 6)
                       if d1_df is not None and len(d1_df) >= 2 else None)
        prev5_close = (round(float(d1_df["close"].iloc[-6]), 6)
                       if d1_df is not None and len(d1_df) >= 6 else None)

        pairs_out[key] = {
            "pills":       pills,
            "mom":         mom,
            "adx":         adx,
            "d1_pct":      d1p,
            "d5_pct":      d5p,
            "prev_close":  prev_close,
            "prev5_close": prev5_close,
            "cont":        cont,
        }
        print(f"  {key}: cont={cont}%")

    # ── Assemble signals.json ─────────────────────────────────────────────────
    # Preserve keys written by scan_news.py — not touched by hourly scan
    preserved = {
        k: prev.get(k)
        for k in ("regime_w1", "macro", "macro_assets",
                  "catalyst", "ranked", "calendar", "week_ahead",
                  "deep_analysis", "breaking", "last_alert", "gold_signal")
        if prev.get(k)
    }

    out = {
        "updated":      now.isoformat(),
        "regime_d1":    regime_d1,
        "regime_h4":    regime_h4,
        "regime_h1":    regime_h1,
        "csm":          csm,
        "correlations": correlations,
        "pairs":        pairs_out,
        **preserved,
    }

    save_signals(out)
    print(f"\n✓ signals.json saved")

    # ── Level alerts + EMA touch alerts ───────────────────────────────────────
    print("\n[Alerts] Checking level alerts + EMA touches…")

    # Build per-pair H4 last bar (high/low/close) and H4 EMA values
    _pair_prices = {}
    _pair_emas   = {}
    for pair in PAIRS:
        key  = pair.replace("/", "")
        sc   = pair_scores.get(key, {})
        h4r  = (sc.get("h4") or {}).get("raw") or {}
        h4df = ohlcv.get(key, {}).get("h4")

        # Use H4 last bar high/low/close for wick-accurate EMA touch detection
        if h4df is not None and len(h4df) >= 1:
            _pair_prices[key] = {
                "high":  float(h4df["high"].iloc[-1]),
                "low":   float(h4df["low"].iloc[-1]),
                "close": float(h4df["close"].iloc[-1]),
            }

        if h4r.get("ema200"):
            _pair_emas[key] = {
                "ema200": h4r["ema200"],
                "ema50":  h4r.get("ema50"),
            }

    # ── Gold signal computation ───────────────────────────────────────────────
    ma          = out.get("macro_assets", {})
    gold_data   = ma.get("gold", {})
    gold_pct    = gold_data.get("delta_pct")
    gold_dir    = gold_data.get("direction", "flat")   # up/down/flat

    h4_regime   = regime_h4.get("regime", "")
    h4_conf     = regime_h4.get("confidence", "Low")
    h1_regime   = regime_h1.get("regime", "")

    # Determine gold signal direction
    RISK_OFF_REGIMES = ("Risk-Off",)
    RISK_ON_REGIMES  = ("Risk-On",)

    if gold_dir == "down" and h4_regime in RISK_OFF_REGIMES:
        gs_direction = "bear"
        h4_confirmed = True
    elif gold_dir == "up" and h4_regime in RISK_ON_REGIMES:
        gs_direction = "bull"
        h4_confirmed = True
    else:
        gs_direction = "neutral"
        h4_confirmed = False

    h1_confirmed = (
        (gs_direction == "bear" and h1_regime in RISK_OFF_REGIMES) or
        (gs_direction == "bull" and h1_regime in RISK_ON_REGIMES)
    )

    gold_signal = {
        "direction":      gs_direction,
        "gold_pct":       round(gold_pct, 2) if gold_pct is not None else None,
        "h4_confirmed":   h4_confirmed,
        "h4_confidence":  h4_conf,
        "h1_confirmed":   h1_confirmed,
        "updated":        now.isoformat(),
    }
    out["gold_signal"] = gold_signal
    print(f"\n🟡 Gold signal: {gs_direction.upper()} | H4: {h4_confirmed} ({h4_conf}) | H1: {h1_confirmed}")

    save_signals(out)

    # ── Telegram: Gold + H4 (Medium/High) + H1 confirmed ─────────────────────
    _pair_closes = {k: v["close"] for k, v in _pair_prices.items()}
    check_levels(_pair_closes, send_telegram)
    # EMA touch alerts removed — Gold signal is the only proactive alert

    if (
        gs_direction != "neutral"
        and h4_confirmed
        and h1_confirmed
        and h4_conf in ("Medium", "High")
    ):
        # Build pair list from ranked top setups
        ranked_top = out.get("ranked", {}).get("top", [])[:3]
        pairs_line = " | ".join(
            f"{r['pair']} {'▲' if r['direction']=='bull' else '▼'}"
            for r in ranked_top
        ) if ranked_top else "—"

        emoji   = "🔴" if gs_direction == "bear" else "🟢"
        dir_lbl = "BEAR — USD bid" if gs_direction == "bear" else "BULL — Risk-On"
        gp_str  = f"{gold_pct:+.1f}%" if gold_pct is not None else ""

        msg = (
            f"{emoji} <b>Gold Signal: {dir_lbl}</b>\n"
            f"Gold {gp_str} | H4 {h4_regime} ({h4_conf}) | H1 {h1_regime}\n"
            f"Setups: {pairs_line}\n"
            f"{now.strftime('%H:%M')} UTC"
        )
        print(f"\n🚨 Gold signal Telegram → {gs_direction.upper()}")
        send_telegram(msg)
        out["last_alert"] = now.isoformat()
        save_signals(out)
    else:
        print(f"\nNo Telegram: direction={gs_direction} h4={h4_confirmed} h1={h1_confirmed} conf={h4_conf}")

    print("=== Hourly Scan complete ===")


if __name__ == "__main__":
    import sys
    if "--test-telegram" in sys.argv:
        print("Sending Telegram test message…")
        send_telegram(
            "\U0001f916 <b>FX Signal Board</b> — Telegram test OK\n"
            "If you see this, bot token + chat ID are correct."
        )
    else:
        main()
