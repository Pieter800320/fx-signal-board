"""
FX Signal Board — Currency Strength Model
Ported from Forex1212 scanner/csm.py for calculation coherence.

D1 CSM : ATR-normalised 14-bar return, D1×0.7 + H4×0.3 blend, 16 pairs
H4 CSM : ATR-normalised 5-bar H4 return, H4×0.8 + H1×0.2 (if H1 available), 16 pairs

These parameters exactly match Forex1212 so both dashboards show the same values.
"""
import numpy as np
import pandas as pd
from scanner.config import CURRENCIES

# ── Parameters — match Forex1212 exactly ─────────────────────────────────────
LOOKBACK   = 14     # D1 CSM: 14-bar price return window
ATR_PERIOD = 14     # ATR smoothing period
D1_WEIGHT  = 0.7   # D1 return weight in combined score
H4_WEIGHT  = 0.3   # H4 return weight in combined score

H4_LOOKBACK = 5    # H4 CSM: 5 H4 bars ≈ 20 hours
H1_LOOKBACK = 8    # H1 component lookback in H4 CSM
H4_CSM_W    = 0.8
H1_CSM_W    = 0.2

# 16-pair set — same as Forex1212 STRENGTH_PAIRS
STRENGTH_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
    "AUD/USD", "USD/CAD", "NZD/USD",
    "AUD/JPY", "NZD/JPY", "CAD/JPY",
    "EUR/GBP", "EUR/CHF", "GBP/CHF",
    "AUD/NZD", "AUD/CAD", "GBP/AUD",
]


# ── ATR helper ────────────────────────────────────────────────────────────────
def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """Simple rolling ATR — matches Forex1212 _atr14() which sums last 14 TRs."""
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs(),
    ], axis=1).max(axis=1)
    val = tr.iloc[-period:].mean()
    return float(val) if not np.isnan(val) else 0.0


# ── ATR-normalised return ─────────────────────────────────────────────────────
def _adj_return(df: pd.DataFrame, lookback: int = LOOKBACK) -> float | None:
    """
    ATR-normalised percentage return over `lookback` bars.
    Matches Forex1212: (close[-1] - close[-lookback-1]) / close[-lookback-1] * 100 / ATR
    """
    if df is None or len(df) < lookback + ATR_PERIOD + 1:
        return None
    c = df["close"].astype(float)
    ret = (c.iloc[-1] - c.iloc[-(lookback + 1)]) / c.iloc[-(lookback + 1)] * 100
    atr = _atr(df)
    return ret / atr if atr > 0 else None


# ── D1 CSM ────────────────────────────────────────────────────────────────────
def compute_csm_d1(ohlcv: dict) -> dict:
    """
    D1 currency strength (0–100, 100=strongest).
    Uses D1 (70%) + H4 (30%) ATR-normalised 14-bar returns across 16 pairs.
    ohlcv keys like "EURUSD" → {"d1": df, "h4": df}
    """
    raw    = {c: [] for c in CURRENCIES}

    for pair in STRENGTH_PAIRS:
        key   = pair.replace("/", "")
        base  = pair[:3]
        quote = pair[3:] if "/" not in pair else pair.split("/")[1]
        base  = pair.split("/")[0]
        quote = pair.split("/")[1]

        d1_ret = _adj_return(ohlcv.get(key, {}).get("d1"))
        h4_ret = _adj_return(ohlcv.get(key, {}).get("h4"))

        if d1_ret is None:
            continue

        combined = (D1_WEIGHT * d1_ret + H4_WEIGHT * h4_ret
                    if h4_ret is not None else d1_ret)

        if base in raw:
            raw[base].append(combined)
        if quote in raw:
            raw[quote].append(-combined)

    return _normalise(raw)


# ── H4 CSM ────────────────────────────────────────────────────────────────────
def compute_csm_h4(ohlcv: dict) -> dict:
    """
    H4 currency strength (0–100, 100=strongest).
    Uses H4 5-bar (80%) + H1 8-bar (20%) ATR-normalised returns across 16 pairs.
    """
    raw = {c: [] for c in CURRENCIES}

    for pair in STRENGTH_PAIRS:
        key   = pair.replace("/", "")
        base  = pair.split("/")[0]
        quote = pair.split("/")[1]

        h4_ret = _adj_return(ohlcv.get(key, {}).get("h4"), lookback=H4_LOOKBACK)
        h1_ret = _adj_return(ohlcv.get(key, {}).get("h1"), lookback=H1_LOOKBACK)

        if h4_ret is None:
            continue

        combined = (H4_CSM_W * h4_ret + H1_CSM_W * h1_ret
                    if h1_ret is not None else h4_ret)

        if base in raw:
            raw[base].append(combined)
        if quote in raw:
            raw[quote].append(-combined)

    return _normalise(raw)


# ── Normalise to 0-100 ────────────────────────────────────────────────────────
def _normalise(raw: dict) -> dict:
    avg    = {c: float(np.mean(v)) if v else 0.0 for c, v in raw.items()}
    vals   = list(avg.values())
    min_v  = min(vals)
    max_v  = max(vals)
    spread = max_v - min_v if max_v != min_v else 1.0
    return {c: round((avg[c] - min_v) / spread * 100, 1) for c in CURRENCIES}


# ── Public entry point ────────────────────────────────────────────────────────
def compute_csm(ohlcv: dict) -> dict:
    """
    Compute both D1 and H4 CSM.
    Returns {"d1": {cur: 0-100}, "h4": {cur: 0-100}}
    """
    return {
        "d1": compute_csm_d1(ohlcv),
        "h4": compute_csm_h4(ohlcv),
    }
