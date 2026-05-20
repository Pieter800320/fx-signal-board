"""
FX Signal Board — hourly regime scanner

Fetches only H4 OHLCV (12 pairs = 12 API calls).
Recomputes H4 regime.
If regime CHANGES, sends Telegram alert + updates signals.json.
If no change, still updates regime_h4 timestamp.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scanner.config     import PAIRS, TF_INTERVAL, TF_BARS
from scanner.fetch      import fetch_ohlcv
from scanner.pills      import classify_all
from scanner.csm        import compute_csm
from scanner.regime     import classify_regime, regime_block_cls
from scanner.cont_score import compute_cont

DELAY = 8  # seconds between API calls


def load_signals() -> dict:
    path = ROOT / "data" / "signals.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_signals(data: dict):
    path = ROOT / "data" / "signals.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def send_telegram(msg: str):
    token  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat   = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("  ⚠ Telegram not configured")
        return
    text = urllib.parse.quote(msg)
    url  = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat}&text={text}&parse_mode=HTML"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            print(f"  Telegram: {r.status}")
    except Exception as e:
        print(f"  Telegram error: {e}")


def regime_emoji(regime: str) -> str:
    return {"Risk-Off": "🔴", "Risk-On": "🟢", "Mixed": "🟡", "Ranging": "⚪"}.get(regime, "")


def main():
    import urllib.parse
    print("=== FX Signal Board — Hourly Regime Check ===")
    now = datetime.now(timezone.utc)

    prev = load_signals()
    prev_h4 = prev.get("regime_h4", {})
    prev_regime_name = prev_h4.get("regime", "Unknown")

    # ── Fetch H4 for all 12 pairs ──────────────────────────────────────────────
    print(f"\nFetching H4 OHLCV for {len(PAIRS)} pairs…")
    ohlcv_h4 = {}
    for i, pair in enumerate(PAIRS):
        key = pair.replace("/", "")
        df  = fetch_ohlcv(pair, TF_INTERVAL["h4"], TF_BARS["h4"])
        if df is not None:
            ohlcv_h4[key] = {"h4": df}
        if i < len(PAIRS) - 1:
            time.sleep(DELAY)
        print(f"  {key}: {'ok' if df is not None else 'ERROR'}")

    # ── H4 pills (h4 only) ────────────────────────────────────────────────────
    pair_pills_h4 = {}
    for key, tfs in ohlcv_h4.items():
        # classify_all only uses available TFs; others come from prev signals
        prev_pills = prev.get("pairs", {}).get(key, {}).get("pills", {})
        h4_pill    = classify_all({"h4": tfs["h4"]}).get("h4", "neutral")
        merged     = {**prev_pills, "h4": h4_pill}
        pair_pills_h4[key] = merged

    # ── H4 CSM ────────────────────────────────────────────────────────────────
    csm_prev = prev.get("csm", {"d1": {}, "h4": {}})
    csm_new  = compute_csm(ohlcv_h4)
    # Merge: keep D1 from previous full scan, update H4
    csm_merged = {"d1": csm_prev.get("d1", {}), "h4": csm_new.get("h4", {})}

    # ── H4 Regime ─────────────────────────────────────────────────────────────
    regime_h4 = classify_regime(csm_merged["h4"], pair_pills_h4, prev_h4)
    new_regime_name = regime_h4["regime"]

    print(f"\nH4 Regime: {regime_h4['regime']} {regime_h4['confidence']} "
          f"(was: {prev_regime_name})")

    # ── Update pairs (H4 pill + cont + cls only) ───────────────────────────────
    pairs_out = prev.get("pairs", {})
    for pair in PAIRS:
        key   = pair.replace("/", "")
        pills = pair_pills_h4.get(key, {})
        adx   = prev.get("pairs", {}).get(key, {}).get("adx")
        cont  = compute_cont(key, pills, adx, csm_merged["d1"], regime_h4)
        cls   = regime_block_cls(key, pills, regime_h4)
        if key in pairs_out:
            pairs_out[key]["pills"]["h4"]  = pills.get("h4", "neutral")
            pairs_out[key]["cont"]         = cont
            pairs_out[key]["regime_cls"]   = cls

    # ── Save ───────────────────────────────────────────────────────────────────
    prev["regime_h4"] = regime_h4
    prev["csm"]       = csm_merged
    prev["pairs"]     = pairs_out
    prev["updated"]   = now.isoformat()
    save_signals(prev)

    # ── Telegram on regime TRANSITION ─────────────────────────────────────────
    if new_regime_name != prev_regime_name and prev_regime_name != "Unknown":
        conf = regime_h4["confidence"]
        msg  = (
            f"{regime_emoji(new_regime_name)} <b>H4 Regime: {new_regime_name}</b> "
            f"({conf})\n"
            f"← was {prev_regime_name}\n"
            f"Score: {regime_h4['score']}/10 · "
            f"{now.strftime('%H:%M')} UTC"
        )
        print(f"\n⚡ Regime transition → Telegram alert")
        send_telegram(msg)
    else:
        print("\nNo regime transition — no Telegram")

    print("=== Hourly check complete ===")


if __name__ == "__main__":
    main()
