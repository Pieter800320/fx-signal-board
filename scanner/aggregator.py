"""
FX Signal Board — OHLCV aggregator
Builds H4 and D1 DataFrames from H1 bars.

H4: groups H1 bars into UTC 4-hour blocks (00/04/08/12/16/20).
    Alignment is exact — Twelvedata H4 bars use the same UTC boundaries.
D1: groups H1 bars by UTC calendar date.
    Note: native D1 bars close at 17:00 NY, not 00:00 UTC. The small
    session-boundary difference is acceptable for trend/momentum signals.

All output DataFrames:
  - Integer-indexed (0..n), oldest first
  - Columns: open, high, low, close  (float)
  - Incomplete current bar is included (most recent data)
"""

import pandas as pd


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Parse datetime column, sort ascending, set as index."""
    out = df.copy()
    out["dt"] = pd.to_datetime(out["datetime"], utc=True)
    out = out.sort_values("dt").set_index("dt")
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def aggregate_h4(h1_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate H1 → H4.
    Groups by UTC 4-hour floor: 00:00 / 04:00 / 08:00 / 12:00 / 16:00 / 20:00.
    """
    df = _prepare(h1_df)
    h4 = df.resample("4h", closed="left", label="left").agg(
        open=("open",  "first"),
        high=("high",  "max"),
        low= ("low",   "min"),
        close=("close","last"),
    ).dropna(subset=["open", "close"])
    h4.index.name = "datetime"
    return h4.reset_index(drop=True)


def aggregate_d1(h1_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate H1 → D1.
    Groups by UTC calendar date.
    """
    df = _prepare(h1_df)
    d1 = df.resample("D", closed="left", label="left").agg(
        open=("open",  "first"),
        high=("high",  "max"),
        low= ("low",   "min"),
        close=("close","last"),
    ).dropna(subset=["open", "close"])
    d1.index.name = "datetime"
    return d1.reset_index(drop=True)


def build_tfs(h1_df: pd.DataFrame) -> dict:
    """
    Build all three timeframes from a single H1 fetch.

    Returns:
        {
            "h1": DataFrame,   # raw H1 bars (integer-indexed)
            "h4": DataFrame,   # aggregated H4
            "d1": DataFrame,   # aggregated D1
        }
    """
    # Normalise H1 to integer index + float columns for consistency
    h1 = _prepare(h1_df).reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        h1[col] = pd.to_numeric(h1[col], errors="coerce")

    return {
        "h1": h1,
        "h4": aggregate_h4(h1_df),
        "d1": aggregate_d1(h1_df),
    }
