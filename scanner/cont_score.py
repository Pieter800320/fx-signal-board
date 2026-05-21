"""
FX Signal Board — Continuation Score (0-100)
Port of Forex1212 computeQAI() to Python.

Six components (weights match Forex1212 exactly):
  1. TF Alignment      35%  - D1/H4/H1 pill direction agreement
  2. Entry Position    23%  - reset_score + atr_percentile (H4-based)
  3. CSM Divergence    16%  - H4 CSM base vs quote spread
  4. Regime Fit        13%  - macro context supports trade direction
  5. Rate Differential  5%  - DEFAULTS TO NEUTRAL (no rates data in FSB)
  6. Session Fit        8%  - is current UTC session optimal for this pair

Gates (applied after weighted sum):
  - ADX < 20  -> score capped at 45
  - Counter-regime trade -> score capped (varies by pair type)
"""

from datetime import datetime, timezone

# Session definitions (UTC hours): start, end (wraps midnight if start > end)
_SESSIONS = {
    "SY": (22, 7),
    "TK": (23, 8),
    "LN": (7,  16),
    "NY": (12, 21),
}

_PAIR_SESSIONS = {
    "EURUSD": ["LN", "NY"],
    "GBPUSD": ["LN", "NY"],
    "USDJPY": ["TK", "NY"],
    "USDCHF": ["LN", "NY"],
    "AUDUSD": ["SY", "TK", "NY"],
    "USDCAD": ["NY"],
    "NZDUSD": ["SY", "TK", "NY"],
    "EURJPY": ["LN", "TK"],
    "GBPJPY": ["LN", "TK"],
    "AUDJPY": ["SY", "TK"],
    "NZDJPY": ["SY", "TK"],
    "CADJPY": ["TK", "NY"],
}

_RISK_BASES  = {"AUD", "NZD", "CAD"}
_SAFE_HAVENS = {"CHF", "JPY"}
_GROWTH_CCYS = {"EUR", "GBP"}


def _active_sessions(utc_hour):
    active = []
    for abbr, (start, end) in _SESSIONS.items():
        if start > end:
            if utc_hour >= start or utc_hour < end:
                active.append(abbr)
        else:
            if start <= utc_hour < end:
                active.append(abbr)
    return active


def _market_closed(utc_weekday, utc_hour):
    if utc_weekday == 5:
        return True
    if utc_weekday == 4 and utc_hour >= 22:
        return True
    if utc_weekday == 6 and utc_hour < 22:
        return True
    return False


def compute_cont(pair, pills, adx, csm_h4, regime_h4,
                 reset_score=None, atr_pct=None):
    """
    Compute continuation score for one pair (0-100).

    pair        : "EURUSD" etc.
    pills       : {"d1": "bear", "h4": "bear_strong", "h1": "bear"}
    adx         : H4 ADX float or None
    csm_h4      : {"USD": 80, "EUR": 20, ...}
    regime_h4   : {"regime": "Risk-Off", ...}
    reset_score : 0-100 from compute_reset_score() or None
    atr_pct     : 0-100 from atr_percentile() or None
    """
    base  = pair[:3]
    quote = pair[3:]

    d1_pill = pills.get("d1", "neutral")
    h4_pill = pills.get("h4", "neutral")
    h1_pill = pills.get("h1", "neutral")

    is_bull = d1_pill in ("bull", "bull_strong")
    is_bear = d1_pill in ("bear", "bear_strong")

    if not is_bull and not is_bear:
        return 0

    d1_strong = "_strong" in d1_pill
    h4_strong = "_strong" in h4_pill

    # 1. TF ALIGNMENT (35%)
    h4m = h4_pill in ("bull", "bull_strong") if is_bull else h4_pill in ("bear", "bear_strong")
    h4n = h4_pill == "neutral"
    h1m = h1_pill in ("bull", "bull_strong") if is_bull else h1_pill in ("bear", "bear_strong")
    h1n = h1_pill == "neutral"

    if   h4m and h1m:               align_score = 10 if (d1_strong or h4_strong) else 9
    elif h4m and h1n:               align_score = 7
    elif h4m and not h1m and not h1n: align_score = 5
    elif h4n and h1m:               align_score = 5
    elif h4n and h1n:               align_score = 3
    else:                           align_score = 1

    # 2. ENTRY POSITION (23%)
    reset_comp = (5  if reset_score is None else
                  2  if reset_score <= 20   else
                  4  if reset_score <= 35   else
                  7  if reset_score <= 50   else 10)
    atr_comp   = (5  if atr_pct is None     else
                  10 if 20 <= atr_pct <= 70 else
                  7  if atr_pct < 20        else 3)
    entry_score = round(reset_comp * 0.6 + atr_comp * 0.4)

    # 3. CSM DIVERGENCE (16%)
    csm_base  = csm_h4.get(base,  50)
    csm_quote = csm_h4.get(quote, 50)
    csm_div   = (csm_base - csm_quote) if is_bull else (csm_quote - csm_base)
    csm_score = (10 if csm_div >= 30 else
                 7  if csm_div >= 15 else
                 5  if csm_div >= 5  else
                 3  if csm_div >= -5 else 1)

    # 4. REGIME FIT (13%)
    regime          = regime_h4.get("regime", "Mixed")
    is_risk_pair    = (base in _RISK_BASES or
                       (quote in _RISK_BASES and base not in _SAFE_HAVENS))
    is_safe_haven   = base in _SAFE_HAVENS or quote in _SAFE_HAVENS
    is_growth_pair  = base in _GROWTH_CCYS or quote in _GROWTH_CCYS

    reg_score = 5
    if regime == "Risk-On":
        if   is_bull and is_risk_pair:   reg_score = 10
        elif is_bull and is_growth_pair: reg_score = 8
        elif is_bull:                    reg_score = 6
        elif is_bear and is_safe_haven:  reg_score = 2
        elif is_bear and is_risk_pair:   reg_score = 3
        elif is_bear:                    reg_score = 4
    elif regime == "Risk-Off":
        if   is_bear and is_safe_haven:  reg_score = 10
        elif is_bear and is_growth_pair: reg_score = 7
        elif is_bear:                    reg_score = 6
        elif is_bull and is_risk_pair:   reg_score = 2
        elif is_bull and is_growth_pair: reg_score = 3
        elif is_bull:                    reg_score = 4
    elif regime == "Ranging":
        reg_score = 4

    # 5. RATE DIFFERENTIAL (5%) - neutral default
    rate_score = 5

    # 6. SESSION FIT (8%)
    now       = datetime.now(timezone.utc)
    closed    = _market_closed(now.weekday(), now.hour)
    active    = _active_sessions(now.hour)
    pair_sess = _PAIR_SESSIONS.get(pair, [])
    sess_match = any(s in pair_sess for s in active)

    sess_score = (3  if closed     else
                  3  if not active else
                  10 if sess_match else 2)

    # Weighted sum
    raw = round((
        align_score * 0.35 +
        entry_score * 0.23 +
        csm_score   * 0.16 +
        reg_score   * 0.13 +
        rate_score  * 0.05 +
        sess_score  * 0.08
    ) * 10)

    # ADX gate
    capped = raw
    if adx is not None and adx < 20:
        capped = min(capped, 45)

    # Regime cap
    if regime == "Risk-Off" and is_bull:
        lim    = 40 if is_risk_pair else 70 if is_safe_haven else 50
        capped = min(capped, lim)
    elif regime == "Risk-On" and is_bear:
        lim    = 40 if is_safe_haven else 70 if is_risk_pair else 50
        capped = min(capped, lim)

    return min(100, max(0, capped))
