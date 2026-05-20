"""
FX Signal Board — Currency Strength Model (CSM)

For each timeframe (D1 / H4):
  1. Compute % return for each pair over the last completed bar
  2. Distribute returns to currencies: base gets +ret, quote gets −ret
  3. Average across all pairs each currency appears in
  4. Normalise to 0–100 (weakest=0, strongest=100)

Returns:
  {
    "d1": {"USD":100, "CHF":68, ...},
    "h4": {"GBP":100, "CHF":75, ...}
  }
"""
from scanner.config import CURRENCIES


def _pct_return(df, bars_back: int = 1) -> float | None:
    """Return of last `bars_back` closed bars."""
    if df is None or len(df) < bars_back + 1:
        return None
    prev  = df["close"].iloc[-(bars_back + 1)]
    close = df["close"].iloc[-1]
    if prev == 0:
        return None
    return (close / prev - 1.0) * 100.0


def compute_csm(pair_ohlcv: dict) -> dict:
    """
    pair_ohlcv = { "EURUSD": {"d1": df, "h4": df, ...}, ... }
    Returns  { "d1": {cur: score}, "h4": {cur: score} }
    """
    result = {}

    for tf, bars_back in (("d1", 1), ("h4", 1)):
        raw    = {c: 0.0 for c in CURRENCIES}
        counts = {c: 0   for c in CURRENCIES}

        for pair_key, tfs in pair_ohlcv.items():
            df = tfs.get(tf)
            if df is None or len(pair_key) != 6:
                continue
            base  = pair_key[:3]
            quote = pair_key[3:]
            ret   = _pct_return(df, bars_back)
            if ret is None:
                continue
            if base in raw:
                raw[base]    += ret
                counts[base] += 1
            if quote in raw:
                raw[quote]    -= ret
                counts[quote] += 1

        avg = {c: raw[c] / counts[c] if counts[c] > 0 else 0.0
               for c in CURRENCIES}

        vals   = list(avg.values())
        min_v  = min(vals)
        max_v  = max(vals)
        spread = max_v - min_v

        if spread == 0:
            result[tf] = {c: 50 for c in CURRENCIES}
        else:
            result[tf] = {c: round((avg[c] - min_v) / spread * 100)
                          for c in CURRENCIES}

    return result
