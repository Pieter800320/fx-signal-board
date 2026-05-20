"""
FX Signal Board — OHLCV fetching via Twelvedata free tier
"""
import os
import time
import urllib.request
import json
import pandas as pd
from scanner.config import PAIRS, CROSS_ASSET, TF_INTERVAL, TF_BARS

API_KEY = os.environ.get("TWELVEDATA_KEY", "")
BASE    = "https://api.twelvedata.com"
DELAY   = 8  # seconds between calls — free tier: 8 req/min


def _get(endpoint: str, params: dict) -> dict:
    params["apikey"] = API_KEY
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}/{endpoint}?{qs}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_ohlcv(symbol: str, interval: str, outputsize: int) -> pd.DataFrame | None:
    """Fetch OHLCV bars for a single symbol. Returns DataFrame or None on error."""
    # Twelvedata uses slash for forex pairs, e.g. EUR/USD
    raw = _get("time_series", {
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": outputsize,
        "order":      "ASC",
        "type":       "price",
    })
    if raw.get("status") == "error" or "values" not in raw:
        print(f"  ⚠ fetch_ohlcv error {symbol} {interval}: {raw.get('message','?')}")
        return None
    df = pd.DataFrame(raw["values"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df.sort_values("datetime", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def fetch_all_pairs(timeframes: list[str]) -> dict:
    """
    Fetch OHLCV for all 12 pairs across specified timeframes.
    Returns: { "EURUSD": {"w1": df, "d1": df, ...}, ... }
    """
    result = {p.replace("/", ""): {} for p in PAIRS}
    total = len(PAIRS) * len(timeframes)
    done  = 0

    for pair in PAIRS:
        symbol = pair  # Twelvedata accepts EUR/USD directly
        key    = pair.replace("/", "")
        for tf in timeframes:
            done += 1
            print(f"  [{done}/{total}] {key} {tf.upper()}")
            df = fetch_ohlcv(symbol, TF_INTERVAL[tf], TF_BARS[tf])
            if df is not None:
                result[key][tf] = df
            if done < total:
                time.sleep(DELAY)

    return result


def fetch_cross_asset() -> dict:
    """
    Fetch latest daily bars for each cross-asset instrument.
    Returns: { "SPX": {"close": ..., "prev_close": ..., "w1_close": ...}, ... }
    """
    out   = {}
    items = list(CROSS_ASSET.items())
    for i, (name, symbol) in enumerate(items):
        print(f"  Cross-asset [{i+1}/{len(items)}] {name} ({symbol})")
        try:
            df = fetch_ohlcv(symbol, "1day", 10)
            if df is not None and len(df) >= 2:
                out[name] = {
                    "close":      float(df["close"].iloc[-1]),
                    "prev_close": float(df["close"].iloc[-2]),
                    "w1_close":   float(df["close"].iloc[max(0, len(df) - 6)]),
                }
        except Exception as e:
            print(f"  ⚠ cross-asset {name}: {e}")
        if i < len(items) - 1:
            time.sleep(12)   # 12s gap → max 5/min, well within free tier limit

    return out
