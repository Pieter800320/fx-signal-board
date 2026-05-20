"""
FX Signal Board — 1212 MOM oscillator
Ported from Forex1212 dashboard index.html for calculation coherence.

Exact formula match:
  momentum = (SMA12_now - SMA12_past) / (12 × ATR14)
  normalised = 50 + 50 × tanh(5.6 × momentum)   [sigmoid, rounds to int]

Delta lookbacks — match Forex1212 exactly:
  W1: 4 bars back   D1: 5 bars back
  H4: 30 bars back  H1: 120 bars back

CMP weights — match Forex1212:
  W1×0.1 + D1×0.4 + H4×0.3 + H1×0.1  (then sigmoid-normalised)
"""
import math
import numpy as np
import pandas as pd

# Delta lookbacks — must match Forex1212
DELTA_LB = {"w1": 4, "d1": 5, "h4": 30, "h1": 120}

# CMP weights — must match Forex1212
CMP_W = {"w1": 0.1, "d1": 0.4, "h4": 0.3, "h1": 0.1}


def _atr14(df: pd.DataFrame) -> float:
    """
    Simple 14-bar ATR — matches Forex1212 _atr14().
    Sums true range for last 14 bars, divides by 14.
    """
    if len(df) < 15:
        return 0.0
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    total = 0.0
    for i in range(len(c) - 14, len(c)):
        tr = max(h[i] - l[i],
                 abs(h[i] - c[i - 1]),
                 abs(l[i] - c[i - 1]))
        total += tr
    return total / 14.0


def _momentum1212(df: pd.DataFrame) -> float:
    """
    Raw momentum value — exact Forex1212 formula:
      smaNow  = mean of last 12 closes
      smaPast = mean of closes[-24:-12]
      return  = (smaNow - smaPast) / (12 × ATR14)
    """
    if df is None or len(df) < 26:
        return 0.0
    closes  = df["close"].values.astype(float)
    sma_now  = closes[-12:].mean()
    sma_past = closes[-24:-12].mean()
    atr      = _atr14(df)
    if atr == 0:
        return 0.0
    return (sma_now - sma_past) / (12.0 * atr)


def _norm1212(m: float) -> int:
    """
    Sigmoid normalisation — exact Forex1212 _norm1212():
      50 + 50 × (e^(5.6m) - 1) / (e^(5.6m) + 1)
    Which equals: 50 + 50 × tanh(2.8m)
    """
    e = math.exp(5.6 * m)
    return round(50.0 + 50.0 * (e - 1.0) / (e + 1.0))


def _mom_at_offset(df: pd.DataFrame, offset: int) -> float:
    """Compute momentum at `offset` bars ago."""
    if df is None or len(df) < 26 + offset:
        return 0.0
    sub = df.iloc[:len(df) - offset] if offset > 0 else df
    return _momentum1212(sub)


def compute_mom(df: pd.DataFrame, tf: str) -> tuple[int | None, int | None]:
    """
    Compute current MOM value and delta for one timeframe.
    Returns (value 0-100, delta) or (None, None) if insufficient data.
    """
    if df is None or len(df) < 26:
        return None, None

    lb  = DELTA_LB.get(tf, 5)
    m   = _momentum1212(df)
    val = _norm1212(m)

    if len(df) >= 26 + lb:
        m_past = _mom_at_offset(df, lb)
        past   = _norm1212(m_past)
        delta  = val - past
    else:
        delta = None

    return val, delta


def compute_all(ohlcv: dict) -> dict:
    """
    Compute MOM for W1/D1/H4/H1 and CMP.
    ohlcv = {"w1": df, "d1": df, "h4": df, "h1": df}

    Returns {
        "w1":22, "d1":28, "h4":19, "h1":31,
        "dw1":-6, "dd1":-8, "dh4":-4, "dh1":9,
        "cmp":24
    }
    CMP uses same formula: sigmoid(_norm) of weighted raw momentums.
    """
    result = {}
    raw_m  = {}

    for tf in ("w1", "d1", "h4", "h1"):
        df = ohlcv.get(tf)
        v, d = compute_mom(df, tf)
        result[tf]       = v
        result[f"d{tf}"] = d
        if df is not None and len(df) >= 26:
            raw_m[tf] = _momentum1212(df)
        else:
            raw_m[tf] = None

    # CMP — weighted raw momentum then sigmoid, matching Forex1212 exactly
    cmp_raw = None
    if all(raw_m[tf] is not None for tf in CMP_W):
        cmp_raw = sum(CMP_W[tf] * raw_m[tf] for tf in CMP_W)
        result["cmp"] = _norm1212(cmp_raw)
    else:
        # Fallback: weighted average of available normalised values
        w_sum = v_sum = 0
        for tf, wt in CMP_W.items():
            v = result.get(tf)
            if v is not None:
                v_sum += v * wt
                w_sum += wt
        result["cmp"] = round(v_sum / w_sum) if w_sum > 0 else None

    return result
