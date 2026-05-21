"""
FX Signal Board — OHLCV fetching via Twelvedata free tier
"""
import os
import time
import urllib.request
import json
import pandas as pd
from scanner.config import PAIRS, TF_INTERVAL, TF_BARS

API_KEY = os.environ.get("TWELVEDATA_KEY", "")
BASE    = "https://api.twelvedata.com"
DELAY   = 8  # seconds between calls — free tier: 8 req/min


def _get(endpoint: str, params: dict, retries: int = 2) -> dict:
    params["apikey"] = API_KEY
    qs  = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}/{endpoint}?{qs}"
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"  ⚠ _get attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(10)
    raise TimeoutError(f"All {retries} attempts failed for {url[:80]}")


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
