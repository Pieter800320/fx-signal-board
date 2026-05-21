# scanner/score.py
# Scoring engine — ported from Forex1212 scanner/score.py
# Produces 5 states: Strong Buy / Buy / Neutral / Sell / Strong Sell
#
# Also contains:
#   compute_reset_score()  — entry position quality (ported from Forex1212 scan_h4.py)
#   atr_percentile()       — volatility position (used by cont_score / computeQAI)
#
# FSB changes vs Forex1212:
#   - No W1 timeframe (W1 dropped from FSB)
#   - Timeframe values: "H1" | "H4" | "D1"
#   - Structure multiplier applied on H4 and D1 (same as Forex1212)
#   - ATR hard filter: H1 only (same as Forex1212)

import pandas as pd
import numpy as np

from scanner.structure import detect_structure

THRESHOLDS = {
    "risk_on":  {"strong": 4.5, "signal": 3.0},
    "risk_off": {"strong": 4.5, "signal": 3.0},
    "ranging":  {"strong": 5.5, "signal": 4.0},
    "mixed":    {"strong": 5.5, "signal": 4.0},
    "unknown":  {"strong": 4.5, "signal": 3.0},
}


# ── Indicator helpers ────────────────────────────────────────────────────────

def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


def _macd(close):
    macd_line   = _ema(close, 12) - _ema(close, 26)
    signal_line = _ema(macd_line, 9)
    return macd_line, signal_line, macd_line - signal_line


def _atr_series(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _dmi(high, low, close, period=14):
    up_move  = high.diff()
    dn_move  = -low.diff()
    plus_dm  = pd.Series(
        np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0),
        index=close.index,
    )
    minus_dm = pd.Series(
        np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0),
        index=close.index,
    )
    atr      = _atr_series(high, low, close, period)
    plus_di  = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).replace(
        [np.inf, -np.inf], np.nan
    )
    return plus_di, minus_di, dx.rolling(period).mean()


def _adx_weight(adx_val):
    """Maps ADX value to score multiplier. < 15 = flat market, no signal."""
    if adx_val is None or np.isnan(float(adx_val)):
        return 0.0
    v = float(adx_val)
    if v < 15: return 0.0
    if v < 20: return 0.5
    if v < 25: return 0.75
    return 1.0


def _ema200_score(last, ema200_val):
    return 1.5 if last > ema200_val else -1.5


def _momentum_group_score(ema50_vote, dmi_vote, macd_vote):
    votes     = [ema50_vote, dmi_vote, macd_vote]
    total     = sum(votes)
    direction = 1 if total > 0 else (-1 if total < 0 else 0)
    if direction == 0:
        return 0.0, 0, {"ema50": ema50_vote, "dmi": dmi_vote, "macd": macd_vote}
    magnitude = 2.0 if abs(total) == 3 else 1.0
    return float(direction * magnitude), direction, {
        "ema50": ema50_vote, "dmi": dmi_vote, "macd": macd_vote
    }


def _rsi_score(rsi_val):
    """Graduated RSI capped at ±1.5 to match EMA200 weight."""
    if rsi_val >= 70: return  1.5
    if rsi_val >= 60: return  1.0
    if rsi_val >= 50: return  0.5
    if rsi_val >= 40: return -0.5
    if rsi_val >= 30: return -1.0
    return -1.5


def _macd_histogram_vote(histogram):
    h = histogram.values
    if len(h) < 3: return 0
    if h[-1] > h[-2] > h[-3]: return  1
    if h[-1] < h[-2] < h[-3]: return -1
    if h[-1] > h[-2]:         return  1
    if h[-1] < h[-2]:         return -1
    return 0


def _check_filters(df):
    """ATR contraction filter — used as hard block on H1 only."""
    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    atr   = _atr_series(high, low, close)
    cur_atr = float(atr.iloc[-1])
    avg_atr = float(atr.iloc[-15:-1].mean())
    reasons = []
    if avg_atr > 0 and cur_atr < 0.70 * avg_atr:
        reasons.append(f"ATR contracted ({cur_atr:.5f} vs avg {avg_atr:.5f})")
    return (len(reasons) == 0), reasons


def score_direction(label):
    if label in ("Buy", "Strong Buy"):   return "bull"
    if label in ("Sell", "Strong Sell"): return "bear"
    return "neutral"


# ── Main scoring function ────────────────────────────────────────────────────

def score_pair(df, timeframe="H4", regime="unknown", swing_n=5):
    """
    Score one OHLCV DataFrame and return a full result dict.

    Parameters
    ----------
    df         : OHLCV DataFrame (integer-indexed, columns: open/high/low/close)
    timeframe  : "H1" | "H4" | "D1"
    regime     : regime string — affects Strong/regular thresholds
    swing_n    : swing pivot lookback (H4 default 5, D1 uses 10)

    Returns None if insufficient data (< 210 bars).
    """
    if df is None or len(df) < 210:
        return None

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    last  = float(close.iloc[-1])

    ema200_s                       = _ema(close, 200)
    ema50_s                        = _ema(close, 50)
    rsi_s                          = _rsi(close)
    macd_line, sig_line, macd_hist = _macd(close)
    plus_di, minus_di, adx_s      = _dmi(high, low, close)
    atr_s                          = _atr_series(high, low, close)

    ema200_val = float(ema200_s.iloc[-1])
    ema50_val  = float(ema50_s.iloc[-1])
    rsi_val    = float(rsi_s.iloc[-1])
    adx_val    = float(adx_s.iloc[-1])
    atr_val    = float(atr_s.iloc[-1])
    atr_avg    = float(atr_s.iloc[-15:-1].mean())
    plus_di_v  = float(plus_di.iloc[-1])
    minus_di_v = float(minus_di.iloc[-1])
    atr_ratio  = round(atr_val / max(atr_avg, 1e-8), 2)
    adx_weight = _adx_weight(adx_val)

    raw_indicators = {
        "close":       round(last, 5),
        "ema200":      round(ema200_val, 5),
        "ema50":       round(ema50_val, 5),
        "rsi":         round(rsi_val, 1),
        "macd_line":   round(float(macd_line.iloc[-1]), 6),
        "macd_signal": round(float(sig_line.iloc[-1]), 6),
        "dmi_plus":    round(plus_di_v, 1),
        "dmi_minus":   round(minus_di_v, 1),
        "adx":         round(adx_val, 1),
    }

    # ── ATR filter: hard block on H1, informational on H4/D1 ────────────────
    passes, filter_reasons = _check_filters(df)
    if not passes and timeframe == "H1":
        return {
            "score": 0, "raw_score": 0.0, "label": "Filtered",
            "direction": "neutral", "filter_ok": False,
            "filter_reasons": filter_reasons, "raw": raw_indicators,
            "signals": {}, "adx_weight": adx_weight, "atr_ratio": atr_ratio,
            "conflict": False,
            "structure": {
                "direction": "neutral", "event": "none",
                "strength": 0.0, "multiplier": 1.0,
            },
            "m_detail": {},
        }
    filter_ok = passes

    # ── Compute component scores ─────────────────────────────────────────────
    ema200_sc  = _ema200_score(last, ema200_val)
    ema50_vote = 1 if last > ema50_val else -1
    dmi_vote   = 1 if plus_di_v > minus_di_v else -1
    macd_vote  = _macd_histogram_vote(macd_hist)

    momentum_sc, momentum_dir, m_detail = _momentum_group_score(
        ema50_vote, dmi_vote, macd_vote
    )
    rsi_sc    = _rsi_score(rsi_val)
    raw_score = round((ema200_sc + momentum_sc + rsi_sc) * adx_weight, 2)

    # ── Structure multiplier (H4 and D1 only) ───────────────────────────────
    structure = {
        "direction": "neutral", "event": "none",
        "strength": 0.0, "multiplier": 1.0,
    }
    conflict = False

    if timeframe in ("H4", "D1"):
        _n = 10 if timeframe == "D1" else swing_n
        structure = detect_structure(df, atr_val, swing_n=_n)
        if structure["direction"] != "neutral" and momentum_dir != 0:
            struct_int = 1 if structure["direction"] == "bull" else -1
            if struct_int != momentum_dir:
                conflict  = True
                structure = dict(structure)
                structure["multiplier"] = 1.0  # remove boost; penalty below

    final_score = round(raw_score * structure["multiplier"], 2)

    # ── TF-aware conflict penalty ─────────────────────────────────────────────
    CONFLICT_PENALTY = {"H4": 2.0, "D1": 1.0}
    if conflict:
        penalty     = CONFLICT_PENALTY.get(timeframe, 0.0)
        final_score = round(final_score - (momentum_dir * penalty), 2)

    # ── 5-state label ─────────────────────────────────────────────────────────
    regime_key = str(regime).lower().split()[0] if regime else "unknown"
    thresh     = THRESHOLDS.get(regime_key, THRESHOLDS["unknown"])
    abs_s = abs(final_score)
    sign  = 1 if final_score >= 0 else -1
    if abs_s >= thresh["strong"]:
        label = "Strong Buy" if sign > 0 else "Strong Sell"
    elif abs_s >= thresh["signal"]:
        label = "Buy" if sign > 0 else "Sell"
    else:
        label = "Neutral"

    direction = score_direction(label)

    signals = {
        "EMA200":    int(np.sign(ema200_sc)),
        "Momentum":  int(np.sign(momentum_sc)),
        "RSI":       int(np.sign(rsi_sc)),
        "EMA50":     ema50_vote,
        "DMI":       dmi_vote,
        "MACD":      macd_vote,
        "Structure": 0 if timeframe == "H1" else (
            1 if structure["direction"] == "bull" else
            (-1 if structure["direction"] == "bear" else 0)
        ),
    }

    return {
        "score":          int(round(final_score)),
        "label":          label,
        "direction":      direction,
        "filter_ok":      filter_ok,
        "filter_reasons": filter_reasons,
        "raw":            raw_indicators,
        "signals":        signals,
        "raw_score":      raw_score,
        "adx_weight":     adx_weight,
        "atr_ratio":      atr_ratio,
        "conflict":       conflict,
        "structure":      structure,
        "m_detail":       m_detail,
    }


# ── Entry position helpers (used by cont_score / computeQAI) ────────────────

def atr_percentile(df, window=52):
    """
    Where is current ATR relative to its recent range?
    Returns 0–100 (percentile). Low = compressed, High = extended.
    Used in computeQAI Entry Position component.
    Ported from Forex1212 scanner/score.py.
    """
    atr = _atr_series(
        df["high"].astype(float),
        df["low"].astype(float),
        df["close"].astype(float),
    ).dropna()
    if len(atr) < window:
        return None
    vals    = atr.iloc[-window:]
    current = float(vals.iloc[-1])
    n_below = int((vals < current).sum())
    return round(n_below / (window - 1) * 100)


def compute_reset_score(closes_array, direction="neutral"):
    """
    Mean-reversion entry quality oscillator. Returns integer 0–100.
    Low score = price has reset toward equilibrium (good entry quality).

    direction: 'bull' → low score when oversold/below mean (good for longs)
               'bear' → low score when overbought/above mean (good for shorts)
               'neutral' → absolute value (original behaviour)

    Ported from Forex1212 scanner/scan_h4.py compute_reset_score().
    """
    period = 20
    if len(closes_array) < period + 14:
        return None

    closes = np.array(closes_array, dtype=float)

    def zscore(arr, n):
        mean = np.mean(arr[-n:])
        std  = np.std(arr[-n:])
        return 0.0 if std == 0 else (arr[-1] - mean) / std

    # RSI-based position proxy
    deltas   = np.diff(closes)
    gains    = np.where(deltas > 0, deltas, 0)
    losses   = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-14:])
    avg_loss = np.mean(losses[-14:])
    rs       = avg_gain / avg_loss if avg_loss != 0 else 100
    rsi      = 100 - (100 / (1 + rs))
    pos_proxy = np.tanh((rsi - 50.0) / 10.0)

    # Price stretch from SMA20
    sma20   = np.mean(closes[-period:])
    stretch = np.tanh((closes[-1] - sma20) / sma20) if sma20 != 0 else 0.0

    # Volatility z-score
    vol_z = np.tanh(zscore(
        np.array([np.std(closes[i - period:i]) for i in range(period, len(closes) + 1)]),
        period,
    ))

    # Momentum (ROC 5 + ROC 10)
    roc5  = (closes[-1] - closes[-6])  / closes[-6]  if closes[-6]  != 0 else 0
    roc10 = (closes[-1] - closes[-11]) / closes[-11] if closes[-11] != 0 else 0
    momentum = np.tanh(roc5 * 0.6 + roc10 * 0.4)

    mom_series = np.array([
        np.tanh(
            ((closes[i] - closes[i - 5])  / closes[i - 5]  if closes[i - 5]  != 0 else 0) * 0.6 +
            ((closes[i] - closes[i - 10]) / closes[i - 10] if closes[i - 10] != 0 else 0) * 0.4
        )
        for i in range(10, len(closes))
    ])
    momentum_slow = np.mean(mom_series[-period:]) if len(mom_series) >= period else momentum

    # Direction-aware components
    if direction in ("bull", "bullish"):
        pos_component     = (pos_proxy + 1) / 2
        stretch_component = (stretch + 1) / 2
    elif direction in ("bear", "bearish"):
        pos_component     = (-pos_proxy + 1) / 2
        stretch_component = (-stretch + 1) / 2
    else:
        pos_component     = abs(pos_proxy)
        stretch_component = abs(stretch)

    mean_rev = (
        0.40 * pos_component +
        0.30 * stretch_component +
        0.20 * (abs(momentum - momentum_slow) / 2) +
        0.10 * abs(vol_z)
    )
    return int(round(100 * mean_rev))
