"""
FX Signal Board — news scanner (runs 4x daily: 06/10/14/21 UTC)

1. Fetch cross-asset data (SPX / VIX / Gold / DXY / US10Y / Copper)
2. Compute W1 backdrop (weekly % changes → Risk-Off / Risk-On / Mixed)
3. Compute macro momentum (daily moves)
4. Call Claude Haiku → 1–2 sentence market summary
5. Update regime_w1, macro, news keys in signals.json
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

from scanner.fetch import fetch_cross_asset

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HAIKU_MODEL   = "claude-haiku-4-5-20251001"


def compute_w1_regime(cross: dict) -> dict:
    """
    W1 regime from weekly % changes of cross-asset instruments.

    Scoring (−3 to +3 per instrument, total / n → normalised 0-10):
      Risk-off signals: VIX rising, Gold rising, DXY rising, SPX falling,
                        Copper falling, US10Y falling (flight to safety)
      Risk-on signals : opposite
    """
    signals = []

    def safe_chg(name, direction="up"):
        d = cross.get(name)
        if not d:
            return 0
        prev = d.get("w1_close", d.get("prev_close"))
        if not prev or prev == 0:
            return 0
        pct = (d["close"] / prev - 1) * 100
        # direction="up" means rising = risk-off signal
        return 1 if (pct > 0) == (direction == "up") else -1

    signals.append(safe_chg("VIX",   "up"))    # VIX up = risk-off
    signals.append(safe_chg("GOLD",  "up"))    # Gold up = risk-off
    signals.append(safe_chg("DXY",   "up"))    # DXY up = risk-off
    signals.append(safe_chg("SPX",   "down"))  # SPX down = risk-off
    signals.append(safe_chg("COPPER","down"))  # Copper down = risk-off
    signals.append(safe_chg("US10Y", "down"))  # Yield down = flight to safety

    ro_count  = signals.count(1)
    ron_count = signals.count(-1)
    net       = ro_count - ron_count  # positive = risk-off

    if net >= 3:
        regime, confidence = "Risk-Off", "High"
    elif net >= 1:
        regime, confidence = "Risk-Off", "Medium"
    elif net <= -3:
        regime, confidence = "Risk-On", "High"
    elif net <= -1:
        regime, confidence = "Risk-On", "Medium"
    else:
        regime, confidence = "Mixed", "Low"

    # Score 0-10: 10=max risk-off, 5=neutral, 0=max risk-on
    score = round(5.0 + net * (5.0 / max(len(signals), 1)), 1)
    score = max(0.0, min(10.0, score))

    return {
        "regime":     regime,
        "confidence": confidence,
        "score":      score,
        "signals":    net,
        "total":      len(signals),
    }


def compute_macro(cross: dict) -> dict:
    """Daily (D1) cross-asset momentum signal."""
    signals = []

    def daily_chg(name, direction="up"):
        d = cross.get(name)
        if not d:
            return 0
        prev = d.get("prev_close")
        if not prev or prev == 0:
            return 0
        pct = (d["close"] / prev - 1) * 100
        return 1 if (pct > 0) == (direction == "up") else -1

    signals.append(daily_chg("VIX",    "up"))
    signals.append(daily_chg("GOLD",   "up"))
    signals.append(daily_chg("DXY",    "up"))
    signals.append(daily_chg("SPX",    "down"))
    signals.append(daily_chg("COPPER", "down"))
    signals.append(daily_chg("US10Y",  "down"))

    net = sum(signals)

    if net >= 3:
        label = "Risk-Off"
    elif net >= 1:
        label = "Risk-Off"
    elif net <= -3:
        label = "Risk-On"
    elif net <= -1:
        label = "Risk-On"
    else:
        label = "Mixed"

    return {
        "label":   label,
        "signals": net,
        "total":   len(signals),
    }


def call_news_summary(cross: dict) -> str:
    """Ask Claude Haiku for a 25-word market summary."""
    if not ANTHROPIC_KEY:
        return "No API key — narrative unavailable."

    def fmt(name):
        d = cross.get(name)
        if not d:
            return f"{name}: n/a"
        prev = d.get("prev_close", d["close"])
        pct  = (d["close"] / prev - 1) * 100 if prev else 0
        return f"{name}: {d['close']:.2f} ({pct:+.1f}%)"

    lines = "\n".join(fmt(n) for n in ("SPX", "VIX", "GOLD", "DXY", "US10Y", "COPPER"))

    prompt = (
        "Cross-asset snapshot (daily change):\n"
        f"{lines}\n\n"
        "Write exactly 1–2 sentences (max 30 words total) summarising the current "
        "macro FX environment and the dominant risk theme. "
        "No preamble, no markdown, plain text only."
    )

    body = json.dumps({
        "model":      HAIKU_MODEL,
        "max_tokens": 80,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read().decode())
            return resp["content"][0]["text"].strip()
    except Exception as e:
        return f"News unavailable ({e})"


def main():
    print("=== FX Signal Board — News Scan ===")
    now = datetime.now(timezone.utc)

    # ── Load existing signals.json ─────────────────────────────────────────────
    sig_path = ROOT / "data" / "signals.json"
    signals  = {}
    if sig_path.exists():
        with open(sig_path) as f:
            signals = json.load(f)

    # ── Fetch cross-asset ──────────────────────────────────────────────────────
    print("\n[1/3] Fetching cross-asset data…")
    cross = fetch_cross_asset()

    # ── W1 Backdrop ───────────────────────────────────────────────────────────
    print("\n[2/3] Computing W1 backdrop + macro momentum…")
    w1  = compute_w1_regime(cross)
    mac = compute_macro(cross)
    print(f"  W1:    {w1['regime']} {w1['confidence']} ({w1['signals']:+d}/{w1['total']})")
    print(f"  Macro: {mac['label']} ({mac['signals']:+d}/{mac['total']})")

    # ── News summary ──────────────────────────────────────────────────────────
    print("\n[3/3] Claude Haiku news summary…")
    text = call_news_summary(cross)
    print(f"  → {text}")

    # ── Patch signals.json ────────────────────────────────────────────────────
    signals["regime_w1"] = w1
    signals["macro"]     = mac
    signals["news"]      = {"text": text, "updated": now.isoformat()}
    signals["updated"]   = now.isoformat()

    with open(sig_path, "w") as f:
        json.dump(signals, f, indent=2)
    print(f"\n✓ Saved {sig_path}")
    print("=== News Scan complete ===")


if __name__ == "__main__":
    main()
