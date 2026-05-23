"""
FX Signal Board — H4 structural regime

Classifies the current market environment from H4 pair scores and CSM.

Four votes are cast:
  1. Safe-haven divergence: (JPY+CHF) vs (AUD+NZD+CAD) from H4 CSM
  2. USD proxy: USD vs risk currencies from H4 CSM
  3. Risk basket: average of risk pair scores from pills
  4. Ranging override: if <40% pairs have directional pills, force Ranging

Output:
  {
    "regime":     "Risk-Off" | "Risk-On" | "Mixed" | "Ranging",
    "confidence": "High" | "Medium" | "Low",
    "score":      float (0-10, higher = more Risk-Off),
    "stable":     bool (same regime as previous scan)
  }
"""
from scanner.config import PAIRS


def classify_regime(csm: dict, pair_pills: dict, prev_regime: dict | None, tf: str = "h4") -> dict:
    """
    csm         — CSM scores for the target TF {cur: 0-100}
    pair_pills  — { "EURUSD": {"d1": "bear_strong"|..., "h4": ..., "h1": ...}, ... }
    prev_regime — previous regime dict (for stability flag)
    tf          — timeframe to read from pair_pills: "d1" | "h4" | "h1"
    """
    # ── Vote 1: safe-haven divergence ─────────────────────────────────────────
    safe_havens = ["JPY", "CHF"]
    risk_currs  = ["AUD", "NZD", "CAD"]

    sh_avg   = sum(csm.get(c, 50) for c in safe_havens)  / len(safe_havens)
    risk_avg = sum(csm.get(c, 50) for c in risk_currs)   / len(risk_currs)

    v1 = "risk_off" if sh_avg > risk_avg + 15 else \
         "risk_on"  if risk_avg > sh_avg + 15 else "mixed"

    # ── Vote 2: USD proxy ──────────────────────────────────────────────────────
    usd = csm.get("USD", 50)
    non_usd_risk = ["EUR", "GBP", "AUD", "NZD"]
    non_usd_avg  = sum(csm.get(c, 50) for c in non_usd_risk) / len(non_usd_risk)

    v2 = "risk_off" if usd > non_usd_avg + 20 else \
         "risk_on"  if non_usd_avg > usd + 20  else "mixed"

    # ── Vote 3: risk basket (pill direction of risk pairs at target TF) ────────
    risk_pairs = ["AUDUSD", "NZDUSD", "GBPUSD", "EURUSD", "AUDJPY", "NZDJPY"]
    bull_count = bear_count = 0
    for p in risk_pairs:
        pill = pair_pills.get(p, {}).get(tf, "neutral")
        if pill in ("bull", "bull_strong"):
            bull_count += 1
        elif pill in ("bear", "bear_strong"):
            bear_count += 1

    v3 = "risk_off" if bear_count > bull_count + 1 else \
         "risk_on"  if bull_count > bear_count + 1 else "mixed"

    # ── Vote 4: ranging override ───────────────────────────────────────────────
    all_pills = [
        pair_pills.get(p, {}).get(tf, "neutral")
        for p in [pair.replace("/", "") for pair in PAIRS]
    ]
    directional = sum(1 for p in all_pills if p != "neutral")
    ranging = directional / len(all_pills) < 0.40

    if ranging:
        return {
            "regime":     "Ranging",
            "confidence": "Low",
            "score":      5.0,
            "stable":     prev_regime.get("regime") == "Ranging" if prev_regime else False,
        }

    # ── Tally votes ────────────────────────────────────────────────────────────
    votes = [v1, v2, v3]
    ro_count  = votes.count("risk_off")
    ron_count = votes.count("risk_on")

    if ro_count >= 2:
        regime     = "Risk-Off"
        confidence = "High" if ro_count == 3 else "Medium"
        score      = round(3.0 + (3.0 - ron_count) * 2.0 + (sh_avg - risk_avg) / 20, 1)
        score      = max(0.0, min(10.0, score))
    elif ron_count >= 2:
        regime     = "Risk-On"
        confidence = "High" if ron_count == 3 else "Medium"
        score      = round(3.0 + (3.0 - ro_count) * 2.0 + (risk_avg - sh_avg) / 20, 1)
        score      = max(0.0, min(10.0, score))
    else:
        regime     = "Mixed"
        confidence = "Low"
        score      = 5.0

    stable = prev_regime.get("regime") == regime if prev_regime else False

    return {
        "regime":     regime,
        "confidence": confidence,
        "score":      score,
        "stable":     stable,
    }
