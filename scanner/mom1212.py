"""
FX Signal Board — 1212 MOM oscillator

ATR-normalised momentum oscillator.
  0   = maximum bearish
  50  = neutral
  100 = maximum bullish

Delta = current value minus value from 1 bar ago.

Algorithm:
  1. True Range and ATR(14)
  2. Normalised return per bar = (close[i] - close[i-1]) / ATR[i]
  3. Momentum at bar i = sum of last LOOKBACK normalised returns
  4. Normalise against 5th/95th percentile of last HISTORY momentum values
     (robust against outliers)
  5. Clamp to 0–100, round to integer
  6. Delta = current - prior (also rounded)

CMP (composite) = weighted average across W1/D1/H4/H1.
Weights: W1×1, D1×2, H4×2, H1×1 → /6
"""
import numpy as np
import pandas as pd

LOOKBACK = 20   # bars of normalised returns to sum
HISTORY  = 200  # bars of history for percentile anchoring


def _atr14(df: pd.DataFrame) -> np.ndarray:
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    tr = np.zeros(len(c))
    tr[0] = h[0] - l[0]
    for i in range(1, len(c)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
    atr = np.zeros(len(c))
    # Simple 14-bar rolling mean
    for i in range(13, len(c)):
        atr[i] = tr[i-13:i+1].mean()
    return atr


def _mom_series(df: pd.DataFrame) -> np.ndarray | None:
    """Compute full momentum series (raw sums, pre-normalisation)."""
    n = len(df)
    if n < 14 + LOOKBACK + 2:
        return None
    closes = df["close"].values.astype(float)
    atr    = _atr14(df)
    norm_ret = np.zeros(n)
    for i in range(1, n):
        if atr[i] > 0:
            norm_ret[i] = (closes[i] - closes[i-1]) / atr[i]
    mom = np.zeros(n)
    for i in range(LOOKBACK, n):
        mom[i] = norm_ret[i-LOOKBACK+1:i+1].sum()
    return mom


def compute_mom(df: pd.DataFrame) -> tuple[int | None, int | None]:
    """
    Compute MOM value and delta for one timeframe.
    Returns (value 0-100, delta) or (None, None) if insufficient data.
    """
    mom = _mom_series(df)
    if mom is None:
        return None, None

    # Use last HISTORY non-zero values for percentile anchoring
    valid_mom = mom[LOOKBACK:]   # first LOOKBACK bars are 0
    if len(valid_mom) < 10:
        return None, None

    anchor = valid_mom[-HISTORY:] if len(valid_mom) >= HISTORY else valid_mom
    p5  = np.percentile(anchor, 5)
    p95 = np.percentile(anchor, 95)

    if p95 == p5:
        return 50, 0

    def norm(x: float) -> int:
        v = (x - p5) / (p95 - p5) * 100
        return int(max(0, min(100, round(v))))

    current = norm(valid_mom[-1])
    prior   = norm(valid_mom[-2]) if len(valid_mom) >= 2 else current

    return current, current - prior


def compute_all(ohlcv: dict) -> dict:
    """
    Compute MOM for W1/D1/H4/H1 and CMP.
    ohlcv = {"w1": df, "d1": df, "h4": df, "h1": df}
    Returns {
        "w1":22, "d1":28, "h4":19, "h1":31,
        "dw1":-6, "dd1":-8, "dh4":-4, "dh1":9,
        "cmp":24
    }
    """
    result = {}
    values = {}

    for tf in ("w1", "d1", "h4", "h1"):
        df = ohlcv.get(tf)
        if df is not None:
            v, d = compute_mom(df)
        else:
            v, d = None, None
        result[tf]        = v
        result[f"d{tf}"]  = d
        if v is not None:
            values[tf] = v

    # CMP: weighted average W1×1, D1×2, H4×2, H1×1
    weights = {"w1": 1, "d1": 2, "h4": 2, "h1": 1}
    w_sum = v_sum = 0
    for tf, wt in weights.items():
        v = values.get(tf)
        if v is not None:
            v_sum += v * wt
            w_sum += wt

    result["cmp"] = round(v_sum / w_sum) if w_sum > 0 else None
    return result
