"""
FX Signal Board — pill classifier
Thin wrapper around score.py (full Forex1212 scoring engine).

Maps Forex1212 labels → FSB pill strings:
  Strong Buy  → bull_strong
  Buy         → bull
  Neutral     → neutral
  Sell        → bear
  Strong Sell → bear_strong
  Filtered    → neutral  (ATR contraction — treat as no signal)
"""

from scanner.score import score_pair

_LABEL_TO_PILL = {
    "Strong Buy":  "bull_strong",
    "Buy":         "bull",
    "Neutral":     "neutral",
    "Sell":        "bear",
    "Strong Sell": "bear_strong",
    "Filtered":    "neutral",
}

_TF_MAP = {"d1": "D1", "h4": "H4", "h1": "H1"}



def classify_full(tfs: dict, regime: str = "unknown") -> dict:
    """
    Classify all timeframes, returning full score_pair result per TF.
    Used by scan_h1.py to extract reset_score, atr_percentile, raw indicators.

    Returns
    -------
    {
        "pills":  {"d1": "bear", "h4": "bear_strong", "h1": "bear"},
        "scores": {"d1": {...}, "h4": {...}, "h1": {...}},
    }
    """
    pills  = {}
    scores = {}
    for tf, df in tfs.items():
        if df is not None and tf in _TF_MAP:
            result = score_pair(df, timeframe=_TF_MAP[tf], regime=regime)
            if result is None:
                pills[tf]  = "neutral"
                scores[tf] = None
            else:
                pills[tf]  = _LABEL_TO_PILL.get(result["label"], "neutral")
                scores[tf] = result
    return {"pills": pills, "scores": scores}
