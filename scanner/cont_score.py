"""
FX Signal Board — Continuation score (0–100)

Five components:

  1. TF Alignment (D1 / H4 / H1)          max 40 pts
     All 3 aligned : 40
     2 of 3        : 25
     1 of 3        : 10

  2. CSM Divergence (D1, base vs quote)   max 20 pts
     Spread > 50   : 20
     Spread > 30   : 14
     Spread > 15   : 7

  3. ADX (directional energy)             max 15 pts
     ADX ≥ 40      : 15
     ADX ≥ 25      : 10
     ADX ≥ 20      : 5

  4. Regime Fit (H4 regime)               max 15 pts
     Strongly fits : 15
     Neutral       : 5
     Opposes       : 0

  5. W1 pill confirms D1                  max 10 pts
     Agrees        : 10

Direction is derived from the D1 pill.  If D1 is neutral the score is 0.
"""
from scanner.config import RISK_ON, RISK_OFF


def compute_cont(
    pair:      str,
    pills:     dict,
    adx:       float | None,
    csm_d1:    dict,
    regime_h4: dict,
) -> int:
    base  = pair[:3]
    quote = pair[3:]

    d1_pill = pills.get("d1", "neutral")
    if d1_pill in ("bull", "bull_strong"):
        direction = "bull"
    elif d1_pill in ("bear", "bear_strong"):
        direction = "bear"
    else:
        return 0   # no D1 conviction

    # ── 1. TF Alignment ────────────────────────────────────────────────────────
    aligned = 0
    for tf in ("d1", "h4", "h1"):
        p = pills.get(tf, "neutral")
        if direction == "bull" and p in ("bull", "bull_strong"):
            aligned += 1
        elif direction == "bear" and p in ("bear", "bear_strong"):
            aligned += 1

    align_pts = 40 if aligned == 3 else 25 if aligned == 2 else 10

    # ── 2. CSM Divergence ──────────────────────────────────────────────────────
    b_score = csm_d1.get(base, 50)
    q_score = csm_d1.get(quote, 50)
    # Effective spread: positive = base is stronger
    eff = b_score - q_score if direction == "bull" else q_score - b_score
    csm_pts = 20 if eff > 50 else 14 if eff > 30 else 7 if eff > 15 else 0

    # ── 3. ADX ─────────────────────────────────────────────────────────────────
    adx = adx or 0
    adx_pts = 15 if adx >= 40 else 10 if adx >= 25 else 5 if adx >= 20 else 0

    # ── 4. Regime Fit ──────────────────────────────────────────────────────────
    regime = regime_h4.get("regime", "Mixed")
    regime_pts = 5  # neutral default

    if regime == "Risk-Off":
        fits_bear = base in RISK_ON  or quote in RISK_OFF
        fits_bull = base in RISK_OFF or quote in RISK_ON
        if   direction == "bear" and fits_bear: regime_pts = 15
        elif direction == "bull" and fits_bull: regime_pts = 15
        elif (direction == "bear" and not fits_bear) or \
             (direction == "bull" and not fits_bull): regime_pts = 0

    elif regime == "Risk-On":
        fits_bull = base in RISK_ON  or quote in RISK_OFF
        fits_bear = base in RISK_OFF or quote in RISK_ON
        if   direction == "bull" and fits_bull: regime_pts = 15
        elif direction == "bear" and fits_bear: regime_pts = 15
        elif (direction == "bull" and not fits_bull) or \
             (direction == "bear" and not fits_bear): regime_pts = 0

    # ── 5. W1 Pill Confirmation ────────────────────────────────────────────────
    w1_pill = pills.get("w1", "neutral")
    w1_pts  = 0
    if direction == "bull" and w1_pill in ("bull", "bull_strong"):
        w1_pts = 10
    elif direction == "bear" and w1_pill in ("bear", "bear_strong"):
        w1_pts = 10

    total = align_pts + csm_pts + adx_pts + regime_pts + w1_pts
    return min(100, max(0, total))
