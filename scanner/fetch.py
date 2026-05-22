"""
FX Signal Board — OHLCV fetching via Twelvedata free tier
"""
import os
import time
import urllib.request
import json
import pandas as pd
from scanner.config import PAIRS, TF_INTERVAL, TF_BARS

API_KEY      = os.environ.get("TWELVEDATA_KEY", "")
BASE         = "https://api.twelvedata.com"
_RATE_TARGET = 6          # max calls/min to scanner — leaves 2/min for browser
_MIN_DELAY   = 60 / _RATE_TARGET   # = 10s minimum spacing
_last_call_ts: float = 0.0         # module-level timestamp of last API call


def _rate_wait():
    """Block until at least _MIN_DELAY seconds have passed since the last call."""
    global _last_call_ts
    elapsed = time.monotonic() - _last_call_ts
    wait    = _MIN_DELAY - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


def _get(endpoint: str, params: dict, retries: int = 3) -> dict:
    params["apikey"] = API_KEY
    qs  = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}/{endpoint}?{qs}"
    for attempt in range(1, retries + 1):
        _rate_wait()
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = json.loads(r.read().decode())
            # Twelvedata returns code:429 for both per-minute AND daily limits.
            # Daily exhaustion is unrecoverable — fail immediately.
            # Per-minute limits are transient — retry with backoff.
            if data.get("code") == 429:
                msg = data.get("message", "").lower()
                if "day" in msg:
                    raise RuntimeError(f"Daily API credit limit reached: {data.get('message')}")
                wait = 20 * attempt
                print(f"  ⚠ 429 per-minute limit — waiting {wait}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue
            return data
        except Exception as e:
            print(f"  ⚠ _get attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(15)
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
            # No manual sleep needed — _rate_wait() inside _get() handles spacing

    return result
