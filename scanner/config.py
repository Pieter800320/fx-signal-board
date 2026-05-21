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

# Cross-asset signals via forex pairs — all available on Twelvedata free tier

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
# W1 dropped — FSB uses D1/H4/H1 only (D1/H4 aggregated from H1)
TF_INTERVAL = {
    "h1": "1h",
}

# 5000 H1 bars covers:
#   H4 EMA200 : 1250 H4 bars  (200 needed)
#   D1 EMA200 : ~208 D1 bars  (200 needed, just sufficient)
#   H1 MOM delta : 146 bars   (OK)
#   H4 MOM delta : 224 bars   (OK)
#   D1 MOM delta : 744 bars   (OK)
TF_BARS = {
    "h1": 5000,
}
