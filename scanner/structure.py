# scanner/structure.py
# Market structure detector — BOS / CHoCH event classification
# Direct port from Forex1212 scanner/structure.py — no modifications.
#
# Fix from audit:
#   - Pivot detection changed from == np.max() to strict left-side comparison
#     with tie-tolerance on right side. Eliminates false pivots on duplicate
#     highs (float equality failure on noisy pairs like GBPJPY).

import numpy as np


def detect_structure(df, atr, swing_n=5):
    """
    Event-based market structure detector.

    Finds genuine swing highs/lows, classifies the last structural event
    as BOS (trend continuation) or CHoCH (potential reversal).

    Parameters
    ----------
    df      : OHLCV DataFrame
    atr     : current ATR value (float)
    swing_n : pivot lookback on each side (default 5)

    Returns
    -------
    dict:
        direction   : 'bull' | 'bear' | 'neutral'
        event       : 'BOS'  | 'CHoCH' | 'none'
        strength    : 0.0 – 1.0
        multiplier  : BOS   → 1.0 + 0.3*strength  (max 1.30)
                      CHoCH → 1.0 - 0.4*strength  (min 0.40)
                      none  → 1.00
    """
    high  = df["high"].values.astype(float)
    low   = df["low"].values.astype(float)
    close = df["close"].values.astype(float)

    _neutral = {"direction": "neutral", "event": "none", "strength": 0.0, "multiplier": 1.0}

    if len(high) < swing_n * 2 + 1:
        return _neutral

    # ── Pivot detection (strict left, tolerant right) ───────────────────────
    swH, swL = [], []
    for i in range(swing_n, len(high) - swing_n):
        left_h  = high[i - swing_n : i]
        right_h = high[i + 1 : i + swing_n + 1]
        left_l  = low[i  - swing_n : i]
        right_l = low[i  + 1 : i + swing_n + 1]

        if len(left_h) == 0 or len(right_h) == 0:
            continue

        # Swing high: strictly above all left bars, >= all right bars
        if high[i] > np.max(left_h) and high[i] >= np.max(right_h):
            swH.append((i, high[i]))
        # Swing low: strictly below all left bars, <= all right bars
        if low[i] < np.min(left_l) and low[i] <= np.min(right_l):
            swL.append((i, low[i]))

    if len(swH) < 2 or len(swL) < 2:
        return _neutral

    # ── Trend from last two confirmed swings ────────────────────────────────
    higher_highs = swH[-1][1] > swH[-2][1]
    higher_lows  = swL[-1][1] > swL[-2][1]
    lower_highs  = swH[-1][1] < swH[-2][1]
    lower_lows   = swL[-1][1] < swL[-2][1]

    if higher_highs and higher_lows:
        trend = "bull"
    elif lower_highs and lower_lows:
        trend = "bear"
    else:
        return _neutral

    last_c = close[-1]
    event  = "none"

    # ── Event classification ─────────────────────────────────────────────────
    if trend == "bear":
        if last_c < swL[-1][1]:   event = "BOS"
        elif last_c > swH[-1][1]: event = "CHoCH"
    else:  # bull
        if last_c > swH[-1][1]:   event = "BOS"
        elif last_c < swL[-1][1]: event = "CHoCH"

    # ── Strength ─────────────────────────────────────────────────────────────
    ref      = swL[-1][1] if trend == "bull" else swH[-1][1]
    atr_safe = max(float(atr), 1e-8)
    strength = round(min(abs(last_c - ref) / atr_safe / 2.0, 1.0), 2)

    # ── Multiplier ───────────────────────────────────────────────────────────
    if event == "BOS":
        multiplier = round(1.0 + 0.3 * strength, 3)              # 1.00 – 1.30
    elif event == "CHoCH":
        multiplier = round(max(1.0 - 0.4 * strength, 0.4), 3)   # 0.40 – 1.00
    else:
        multiplier = 1.0

    return {
        "direction":  trend,
        "event":      event,
        "strength":   strength,
        "multiplier": multiplier,
    }
