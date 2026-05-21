"""
FX Signal Board — pairwise correlation
Port of Forex1212 scanner/correlate.py — no modifications.

Computes Pearson correlation on H4 returns across all 12 pairs.
Uses last LOOKBACK bars (~8 trading days on H4).

Output: {
    "pairs":  ["EURUSD", "GBPUSD", ...],
    "matrix": [[1.0, 0.85, ...], ...]   # 12x12, rounded to 2dp
}
"""

import numpy as np
from scanner.config import PAIRS

LOOKBACK = 50   # H4 bars — ~8 trading days


def _returns(df, lookback=LOOKBACK):
    if df is None or len(df) < lookback + 1:
        return None
    close = df["close"].astype(float).iloc[-(lookback + 1):]
    return close.pct_change().dropna().values


def compute_correlation(ohlcv: dict) -> dict:
    """
    ohlcv: { "EURUSD": {"h4": df, ...}, ... }

    Returns:
        {
            "pairs":  ["EURUSD", ...],
            "matrix": [[1.0, 0.85, ...], ...]
        }
    """
    labels = [p.replace("/", "") for p in PAIRS]
    rets   = [_returns(ohlcv.get(lbl, {}).get("h4")) for lbl in labels]

    n      = len(labels)
    matrix = [[None] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
                continue
            ri, rj = rets[i], rets[j]
            if ri is None or rj is None:
                matrix[i][j] = None
                continue
            min_len = min(len(ri), len(rj))
            if min_len < 10:
                matrix[i][j] = None
                continue
            corr = float(np.corrcoef(ri[-min_len:], rj[-min_len:])[0, 1])
            matrix[i][j] = round(corr, 2)

    return {"pairs": labels, "matrix": matrix}
