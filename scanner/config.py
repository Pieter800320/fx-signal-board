"""
FX Signal Board — configuration
"""

PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
    "AUD/USD", "USD/CAD", "NZD/USD",
    "EUR/JPY", "GBP/JPY", "AUD/JPY", "NZD/JPY", "CAD/JPY",
]

CURRENCIES = ["GBP", "EUR", "AUD", "NZD", "CAD", "JPY", "CHF", "USD"]

# Risk-on and risk-off classification for regime fit scoring
RISK_ON  = {"AUD", "NZD", "GBP", "EUR", "CAD"}
RISK_OFF = {"JPY", "CHF", "USD"}

# Cross-asset symbols for W1 backdrop + macro momentum (Twelvedata free tier)
# Using ETF proxies — scoring logic unchanged (direction relationships hold)
CROSS_ASSET = {
    "SPX":   "SPY",      # S&P 500 ETF      — up = risk-on
    "VIX":   "VIXY",     # VIX Long ETF     — up = risk-off
    "GOLD":  "XAU/USD",  # Gold spot        — up = risk-off
    "DXY":   "UUP",      # USD Bullish ETF  — up = risk-off
    "US10Y": "TLT",      # 20yr Treasury    — up = yields down = risk-off (flight to safety)
    "COPPER":"CPER",     # Copper ETF       — up = risk-on
}

# Static correlates per pair (pair, direction_same_as_pair)
CORRELATES = {
    "EURUSD": [("GBPUSD", True),  ("NZDUSD", True)],
    "GBPUSD": [("EURUSD", True),  ("GBPJPY", True)],
    "USDJPY": [("EURJPY", True),  ("GBPJPY", True)],
    "USDCHF": [("USDJPY", True),  ("EURUSD", False)],
    "AUDUSD": [("NZDUSD", True),  ("AUDJPY", True)],
    "USDCAD": [("CADJPY", False), ("USDJPY", True)],
    "NZDUSD": [("AUDUSD", True),  ("NZDJPY", True)],
    "EURJPY": [("EURUSD", True),  ("USDJPY", False)],
    "GBPJPY": [("GBPUSD", True),  ("USDJPY", False)],
    "AUDJPY": [("AUDUSD", True),  ("NZDJPY", True)],
    "NZDJPY": [("NZDUSD", True),  ("AUDJPY", True)],
    "CADJPY": [("USDCAD", False), ("USDJPY", False)],
}

# Timeframe intervals for Twelvedata
TF_INTERVAL = {
    "w1": "1week",
    "d1": "1day",
    "h4": "4h",
    "h1": "1h",
}

# Bars needed per TF (enough for EMA200 + MOM lookback + history)
TF_BARS = {
    "w1": 300,
    "d1": 400,
    "h4": 500,
    "h1": 300,
}
