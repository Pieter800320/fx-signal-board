"""
FX Signal Board — pill classification
pill = bull_strong | bull | neutral | bear | bear_strong

Same logic across all TFs (W1/D1/H4/H1):
  - EMA200: long-term trend anchor
  - EMA50:  medium-term structure
  - RSI14:  momentum confirmation

bull_strong : price > EMA200, EMA50 > EMA200, RSI > 55
bull        : price > EMA50, RSI > 52
neutral     : neither clearly bullish nor bearish
bear        : price < EMA50, RSI < 48
bear_strong : price < EMA200, EMA50 < EMA200, RSI < 45
"""
import numpy as np
import pandas as pd


def _ema(series: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out   = np.empty_like(series)
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = alpha * series[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 2:
        return 50.0
    delta  = np.diff(closes[-(period + 2):])
    gains  = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    ag = gains[-period:].mean()
    al = losses[-period:].mean()
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def classify_pill(df: pd.DataFrame) -> str:
    """
    Classify a single timeframe OHLCV DataFrame.
    Requires at least 220 rows for EMA200 to be meaningful.
    """
    if df is None or len(df) < 60:
        return "neutral"

    closes = df["close"].values.astype(float)

    # Use all available data for EMAs; min 220 for EMA200 meaningfulness
    e200 = _ema(closes, 200)[-1] if len(closes) >= 200 else None
    e50  = _ema(closes, 50)[-1]  if len(closes) >= 50  else None

    if e50 is None:
        return "neutral"

    rsi = _rsi(closes)
    c   = closes[-1]

    # Strong classifications require EMA200
    if e200 is not None:
        if c > e200 and c > e50 and e50 > e200 and rsi > 55:
            return "bull_strong"
        if c < e200 and c < e50 and e50 < e200 and rsi < 45:
            return "bear_strong"

    # Regular classifications
    if c > e50 and rsi > 52:
        return "bull"
    if c < e50 and rsi < 48:
        return "bear"

    return "neutral"


def classify_all(ohlcv: dict) -> dict:
    """
    Classify pills for all available timeframes.
    ohlcv = {"w1": df, "d1": df, "h4": df, "h1": df}
    Returns {"w1": "bear_strong", "d1": "bear", ...}
    """
    return {tf: classify_pill(ohlcv.get(tf)) for tf in ("w1", "d1", "h4", "h1")}
