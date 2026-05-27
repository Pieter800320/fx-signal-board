"""
FX Signal Board — news scanner (runs every 2 hours via GitHub Actions)

1. Fetch cross-asset data via Yahoo Finance v8 API (VIX, SPX, Gold, DXY, Copper, US10Y)
   — free, no API key, works from GitHub Actions
2. Compute W1 backdrop + macro momentum from real cross-asset data
3. Fetch RSS headlines
4. Fetch Twelvedata economic calendar
5. Haiku call 1 → News bar: themes + biggest event
6. Haiku call 2 → Analysis bar: data tension from signals.json
"""
import json, os, sys, time, xml.etree.ElementTree as ET, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TWELVEDATA    = os.environ.get("TWELVEDATA_KEY", "")
HAIKU_MODEL   = "claude-haiku-4-5-20251001"
SONNET_MODEL  = "claude-sonnet-4-20250514"

# Yahoo Finance v8 — no API key needed
# (key, yf_symbol, label, risk_off_when_up)
MACRO_INSTRUMENTS = [
    ("vix",    "^VIX",    "VIX",      True),   # up = fear = risk-off
    ("spx",    "^GSPC",   "S&P 500",  False),  # down = risk-off
    ("gold",   "GC=F",    "Gold",     True),   # up = safe haven = risk-off
    ("dxy",    "DX-Y.NYB","DXY",      True),   # up = USD strength = risk-off
    ("copper", "HG=F",    "Copper",   False),  # down = growth fear = risk-off
    ("us10y",  "^TNX",    "US 10Y",   False),  # down = flight to safety = risk-off
    ("us3m",   "^IRX",    "US 3M",    False),  # 3M T-bill; short-end rate signal
    ("wti",    "CL=F",    "WTI Oil",  False),  # up = risk-on demand; key CAD driver
    ("btc",    "BTC-USD", "Bitcoin",  False),  # risk appetite velocity proxy
]

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":     "application/json",
}


# ── Yahoo Finance fetch ───────────────────────────────────────────────────────
def fetch_yf(symbol: str) -> dict | None:
    """Fetch last 10 daily bars via Yahoo Finance v8. Returns {close, prev_close, w1_close} or None."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=15d"
    try:
        req = urllib.request.Request(url, headers=YF_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None
        return {
            "close":      closes[-1],
            "prev_close": closes[-2],
            "w1_close":   closes[max(0, len(closes) - 6)],
        }
    except Exception as e:
        print(f"  ⚠ Yahoo {symbol}: {e}")
        return None


def fetch_all_macro() -> dict:
    """Fetch all macro instruments. Returns {key: {close, prev_close, w1_close, label}}"""
    macro = {}
    for key, symbol, label, risk_off_up in MACRO_INSTRUMENTS:
        print(f"  [{label}] {symbol}")
        d = fetch_yf(symbol)
        if d:
            d["label"]       = label
            macro[key]       = d
            pct = (d["close"] / d["prev_close"] - 1) * 100 if d["prev_close"] else 0
            print(f"    → {d['close']:.4g} ({pct:+.2f}%)")
        time.sleep(2)  # light throttle — no strict rate limit on YF
    print(f"  Macro: {len(macro)}/{len(MACRO_INSTRUMENTS)} fetched")
    return macro


def build_macro_assets(macro: dict) -> dict:
    """
    Build per-asset summary for rank.py: value, delta, direction.
    Also computes yield curve spread (10Y-2Y) from fetched yields.
    """
    out = {}
    for key, d in macro.items():
        if not d or not d.get("close") or not d.get("prev_close"):
            continue
        close = d["close"]
        prev  = d["prev_close"]
        label = d.get("label", key.upper())

        if key in ("us10y", "us3m"):
            # Yields in %, delta in basis points
            bp = (close - prev) * 100
            direction = "up" if bp > 3.0 else "down" if bp < -3.0 else "flat"
            out[key] = {"value": round(close, 3), "delta_bp": round(bp, 1),
                        "direction": direction, "label": label}

        elif key == "vix":
            pct = (close / prev - 1) * 100
            if close > 20 or pct > 3.0:
                direction = "up"
            elif close < 15 or pct < -3.0:
                direction = "down"
            else:
                direction = "flat"
            out[key] = {"value": round(close, 2), "delta_pct": round(pct, 1),
                        "direction": direction, "label": label}

        elif key == "btc":
            pct = (close / prev - 1) * 100
            direction = "up" if pct > 1.0 else "down" if pct < -1.0 else "flat"
            out[key] = {"value": round(close, 0), "delta_pct": round(pct, 1),
                        "direction": direction, "label": label}

        else:
            pct = (close / prev - 1) * 100
            direction = "up" if pct > 0.5 else "down" if pct < -0.5 else "flat"
            dec = 2 if close > 100 else 4
            out[key] = {"value": round(close, dec), "delta_pct": round(pct, 1),
                        "direction": direction, "label": label}

    # ── Yield curve spread (10Y − 2Y) — computed, no extra fetch ─────────────
    y10 = macro.get("us10y", {})
    y2  = macro.get("us3m",  {})
    if y10.get("close") and y2.get("close"):
        spread     = round((y10["close"] - y2["close"]) * 100, 1)   # basis points
        prev_sp    = round(((y10.get("prev_close", y10["close"]) -
                             y2.get("prev_close",  y2["close"])) * 100), 1)
        delta_bp   = round(spread - prev_sp, 1)
        direction  = "up" if delta_bp > 2.0 else "down" if delta_bp < -2.0 else "flat"
        out["curve"] = {"value": spread, "delta_bp": delta_bp,
                        "direction": direction, "label": "10Y-3M"}

    return out


# ── W1 BACKDROP ───────────────────────────────────────────────────────────────
def compute_w1_regime(macro: dict, prev_w1: dict | None = None) -> dict:
    """
    W1 regime from weekly % changes.
    Matches Forex1212 compute_w1_regime() thresholds.
    """
    scores = []

    def score(key, up_threshold, down_threshold, invert=False):
        d = macro.get(key)
        if not d:
            return
        prev = d.get("w1_close", d.get("prev_close"))
        if not prev or prev == 0:
            return
        pct = (d["close"] / prev - 1) * 100
        if not invert:
            if pct > up_threshold:   scores.append(1)   # risk-off
            elif pct < down_threshold: scores.append(-1) # risk-on
        else:
            if pct > up_threshold:   scores.append(-1)  # risk-on
            elif pct < down_threshold: scores.append(1) # risk-off

    # Thresholds match Forex1212 compute_w1_regime()
    score("spx",    3.0,  -3.0, invert=True)   # SPX > +3% = risk-on
    score("vix",   20.0, -15.0)                 # VIX > +20% = risk-off
    score("gold",   4.0,  -3.0)                 # Gold > +4% = risk-off
    score("dxy",    1.5,  -1.5)                 # DXY > +1.5% = risk-off
    score("copper", 2.0,  -2.0, invert=True)    # Copper > +2% = risk-on
    score("us10y",  0.5,  -0.5, invert=True)    # Yield > +0.5% = risk-on

    if not scores:
        return {"regime": "Mixed", "confidence": "Low", "score": 5.0,
                "signals": 0, "total": 0, "stable": False}

    net = sum(scores)
    n   = len(scores)

    if net >= 3:   regime, confidence = "Risk-Off", "High"
    elif net >= 1: regime, confidence = "Risk-Off", "Medium"
    elif net <= -3:regime, confidence = "Risk-On",  "High"
    elif net <= -1:regime, confidence = "Risk-On",  "Medium"
    else:          regime, confidence = "Mixed",     "Low"

    score_val = round(5.0 + net / n * 5.0, 1)
    stable    = (prev_w1 or {}).get("regime") == regime
    return {"regime": regime, "confidence": confidence,
            "score": max(0.0, min(10.0, score_val)),
            "signals": net, "total": n, "stable": stable}


# ── SESSION DETECTION ─────────────────────────────────────────────────────────
def current_session(now: datetime) -> str:
    """Returns the active FX session based on UTC hour."""
    h = now.hour
    if 7 <= h < 8:    return "London open"
    if 8 <= h < 12:   return "London session"
    if 12 <= h < 16:  return "London/NY overlap"
    if 16 <= h < 21:  return "NY session"
    if 21 <= h or h < 7: return "Asian session"  # Sydney opens 21:00 UTC
    return "Off-hours"


# ── MACRO MOMENTUM (daily, institutional thresholds) ──────────────────────────
def compute_macro(macro: dict, prev_mac: dict | None = None) -> dict:
    """
    D1 cross-asset momentum with institutional magnitude thresholds.
    Below threshold = abstain (not counted), not neutral.
    Confidence reflects how many instruments agree.
    """
    scores = []

    def score(key, threshold_pct, invert=False, level_check=None):
        """
        threshold_pct  — minimum absolute % change to cast a vote
        level_check    — optional (low, high) absolute level thresholds
                         e.g. VIX: vote risk-off if level > 20, risk-on if < 15
        """
        d = macro.get(key)
        if not d or not d.get("prev_close") or not d["prev_close"]:
            return
        pct   = (d["close"] / d["prev_close"] - 1) * 100
        abspct = abs(pct)

        # Level-based override (VIX regime zones)
        if level_check:
            lo, hi = level_check
            if d["close"] > hi:
                scores.append(1)   # risk-off zone
                return
            if d["close"] < lo:
                scores.append(-1)  # risk-on / complacency zone
                return

        # Magnitude filter — abstain if move is too small
        if abspct < threshold_pct:
            return

        up       = pct > 0
        risk_off = up if not invert else not up
        scores.append(1 if risk_off else -1)

    # US10Y uses basis-point change, not pct — compute separately
    def score_yield(key, bp_threshold):
        d = macro.get(key)
        if not d or not d.get("prev_close") or not d["prev_close"]:
            return
        bp_change = (d["close"] - d["prev_close"]) * 100  # yield in %, so *100 = bps
        if abs(bp_change) < bp_threshold:
            return
        risk_off = bp_change < 0  # yield down = flight to safety = risk-off
        scores.append(1 if risk_off else -1)

    score("vix",    threshold_pct=5.0,  level_check=(15.0, 20.0))  # level zones override
    score("spx",    threshold_pct=0.8,  invert=True)
    score("gold",   threshold_pct=0.5)
    score("dxy",    threshold_pct=0.3)
    score("copper", threshold_pct=0.8,  invert=True)
    score_yield("us10y", bp_threshold=5.0)
    score_yield("us3m",  bp_threshold=8.0)   # rising 3M = Fed hawkish = risk-off

    net   = sum(scores)
    total = len(scores)  # only instruments that voted

    label      = "Risk-Off" if net > 0 else "Risk-On" if net < 0 else "Mixed"
    abs_net    = abs(net)
    confidence = "High"   if abs_net >= 3 else \
                 "Medium" if abs_net == 2 else \
                 "Low"    if abs_net == 1 else "Neutral"
    stable     = (prev_mac or {}).get("label") == label

    return {"label": label, "signals": net, "total": total,
            "confidence": confidence, "stable": stable}


# ── RSS HEADLINES ─────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://www.forexlive.com/feed/news",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=EURUSD%3DX&region=US&lang=en-US",
]

def fetch_headlines(max_per_feed: int = 6) -> list[str]:
    headlines = []
    for url in RSS_FEEDS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 FX-Signal-Board/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                raw  = r.read().decode("utf-8", errors="replace")
            root = ET.fromstring(raw)
            ns   = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            count = 0
            for item in items:
                if count >= max_per_feed:
                    break
                title = (item.findtext("title") or
                         item.findtext("atom:title", namespaces=ns) or "").strip()
                if title and len(title) > 10:
                    headlines.append(title)
                    count += 1
        except Exception as e:
            print(f"  ⚠ RSS {url[:50]}: {e}")
    seen, unique = set(), []
    for h in headlines:
        k = h.lower()[:60]
        if k not in seen:
            seen.add(k)
            unique.append(h)
    print(f"  Headlines: {len(unique)}")
    return unique[:15]


# ── ECONOMIC CALENDAR ─────────────────────────────────────────────────────────

# Country code → currency code mapping for calendar events
_COUNTRY_CCY = {
    "US": "USD", "EU": "EUR", "DE": "EUR", "FR": "EUR", "IT": "EUR",
    "GB": "GBP", "JP": "JPY", "AU": "AUD", "NZ": "NZD",
    "CA": "CAD", "CH": "CHF",
}

def call_calendar_search(now: datetime) -> list[dict]:
    """
    Single Haiku + web search call: fetch high-impact FX events for the current
    week AND generate a brief interpretation note per event.
    Returns list of event dicts ready for signals.json calendar.events.
    """
    from datetime import timedelta
    monday   = now - timedelta(days=now.weekday())
    friday   = monday + timedelta(days=4)
    date_range = f"{monday.strftime('%B %d')} to {friday.strftime('%B %d, %Y')}"

    prompt = (
        f"Search for 'forex factory economic calendar this week' and 'high impact forex events this week'. "
        f"I need high-impact FX events for the current week ({date_range}). "
        f"Include: central bank decisions (Fed, ECB, BOJ, BOE, RBA, BOC, RBNZ, SNB), "
        f"US NFP, CPI, PCE, GDP, PMI, retail sales, JOLTS, ADP, PPI, unemployment claims. "
        f"Return ONLY a JSON array (no markdown, no backticks) of up to 10 events. "
        f"Each item: currency (3-letter), name, day (Mon/Tue/Wed/Thu/Fri), "
        f"time (HH:MM UTC), date (YYYY-MM-DD), forecast, previous, note (max 8 words, expected FX impact). "
        f"Only include events between {monday.strftime('%Y-%m-%d')} and {friday.strftime('%Y-%m-%d')}."
    )
    raw = _haiku_search(prompt, max_tokens=900)

    def _parse(text: str) -> list:
        try:
            start = text.find('[')
            end   = text.rfind(']') + 1
            if start < 0 or end <= start:
                return []
            return json.loads(text[start:end])
        except Exception:
            return []

    events_raw = _parse(raw)

    # Retry once with simpler query if first attempt returned nothing
    if not events_raw:
        print("  ⚠ Calendar: first search empty — retrying…")
        retry = (
            f"Search 'investing.com economic calendar' or 'dailyfx calendar' for high impact events this week. "
            f"Return JSON array only: currency, name, day, time, date (YYYY-MM-DD), forecast, previous, note. No markdown."
        )
        events_raw = _parse(_haiku_search(retry, max_tokens=900))

    out = []
    for ev in events_raw[:10]:
        date_str = ev.get('date', '')
        time_str = ev.get('time', '00:00')
        try:
            iso = f"{date_str}T{time_str}:00+00:00"
            dt  = datetime.fromisoformat(iso)
            day = dt.strftime('%a')
        except Exception:
            day = ev.get('day', '')
            iso = ''
        ccy = _COUNTRY_CCY.get(ev.get('currency','').upper(), ev.get('currency',''))
        out.append({
            'day':      day or ev.get('day', ''),
            'time':     time_str,
            'iso':      iso,
            'currency': ccy,
            'name':     ev.get('name', ''),
            'forecast': str(ev.get('forecast', '')),
            'previous': str(ev.get('previous', '')),
            'note':     ev.get('note', ''),
        })
    print(f"  Calendar events: {len(out)}")
    return out





# ── HAIKU CALLS ───────────────────────────────────────────────────────────────
def _claude(model: str, system: str, prompt: str, max_tokens: int) -> str:
    if not ANTHROPIC_KEY:
        return "—"
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"Content-Type": "application/json",
                 "x-api-key": ANTHROPIC_KEY,
                 "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["content"][0]["text"].strip()
    except Exception as e:
        return f"Unavailable ({e})"

def _haiku(prompt: str, max_tokens: int = 120) -> str:
    return _claude(HAIKU_MODEL, "", prompt, max_tokens)

def _sonnet(system: str, prompt: str, max_tokens: int = 300) -> str:
    return _claude(SONNET_MODEL, system, prompt, max_tokens)


def _haiku_search(prompt: str, max_tokens: int = 400) -> str:
    """Haiku call with web_search tool enabled. Extracts all text blocks."""
    if not ANTHROPIC_KEY:
        return "—"
    body = json.dumps({
        "model": HAIKU_MODEL,
        "max_tokens": max_tokens,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"Content-Type":      "application/json",
                 "x-api-key":          ANTHROPIC_KEY,
                 "anthropic-version":  "2023-06-01",
                 "anthropic-beta":     "web-search-2025-03-05"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        # Collect all text blocks — web search responses have multiple content blocks
        texts = [b["text"].strip() for b in data.get("content", [])
                 if b.get("type") == "text" and b.get("text", "").strip()]
        if not texts:
            print("  ⚠ _haiku_search: valid response but no text blocks returned")
            return "—"
        return " ".join(texts)
    except Exception as e:
        print(f"  ⚠ _haiku_search error: {e}")
        return f"Unavailable ({e})"


# ── DEEP ANALYSIS — instrument zone context ───────────────────────────────────
# Each entry: list of (upper_bound, label) — first threshold where value <= upper wins.
_ZONES: dict[str, list[tuple]] = {
    "vix":    [(12,    "ultra-complacent — below long-run average, no fear priced"),
               (15,    "complacent — markets relaxed, risk-on bias"),
               (20,    "normal — moderate uncertainty, neutral"),
               (25,    "elevated — stress building, hedging emerging"),
               (30,    "high fear — institutional hedging active"),
               (9999,  "extreme fear — crisis mode, systematic de-risking")],
    "us10y":  [(3.0,   "accommodative — historically supports risk assets"),
               (3.5,   "below neutral — mild bond support"),
               (4.0,   "near neutral Fed rate"),
               (4.5,   "restrictive — mortgage and credit stress building"),
               (9999,  "highly restrictive — historically precedes economic slowdown")],
    "us3m":   [(3.5,   "low short-end — easy financial conditions"),
               (4.5,   "moderate — Fed in holding pattern"),
               (5.5,   "tight — money market stress possible"),
               (9999,  "very tight — cash expensive, risk appetite suppressed")],
    "curve":  [(-100,  "deeply inverted — severe recession signal, Fed cuts priced"),
               (-25,   "inverted — bond market pricing Fed cuts, recession risk elevated"),
               (0,     "flat — transitional zone, directional uncertainty"),
               (50,    "shallow positive — normalising toward historical mean"),
               (9999,  "steep — growth expectations rising, typically risk-on")],
    "gold":   [(1800,  "low — USD alternative demand weak, risk-on tilt"),
               (2100,  "moderate — balanced safe haven demand"),
               (2500,  "elevated — safe haven demand building"),
               (3000,  "high — strong institutional flight to safety"),
               (99999, "extreme — crisis-level safe haven demand, USD alternative surging")],
    "dxy":    [(95,    "very weak — broad USD pressure, risk-on and EM tailwind"),
               (100,   "weak — USD below key parity zone"),
               (104,   "neutral range — balanced flows"),
               (107,   "strong — USD broad bid, risk-off tilt, EM headwind"),
               (9999,  "very strong — USD squeeze, EM stress, global liquidity tightening")],
    "wti":    [(55,    "low — demand concern, global slowdown signalled"),
               (70,    "moderate — balanced supply and demand"),
               (80,    "elevated — energy inflation risk emerging"),
               (95,    "high — stagflation risk, CAD tailwind strong"),
               (9999,  "very high — energy shock territory, stagflation risk")],
    "copper": [(3.0,   "low — growth alarm, industrial demand collapsing"),
               (3.8,   "below average — soft growth signal, AUD headwind"),
               (4.5,   "average — neutral growth outlook"),
               (5.0,   "above average — growth acceleration signal, AUD tailwind"),
               (9999,  "very strong — industrial demand surge, global growth optimism")],
    "spx":    [(4000,  "bear zone — recession pricing, risk-off"),
               (4500,  "recovering — cautious growth optimism"),
               (5000,  "bull range — growth confidence, risk-on"),
               (5500,  "extended — complacency risk elevated"),
               (99999, "extreme — parabolic risk, correction vulnerability high")],
    "btc":    [(30000,  "low — risk-off, crypto winter sentiment"),
               (60000,  "recovery — risk appetite returning"),
               (90000,  "strong — risk-on velocity, institutional inflows"),
               (999999, "extreme — speculative peak risk")],
}


def _get_zone(value: float, key: str) -> str:
    """Return zone label for a given instrument value."""
    for threshold, label in _ZONES.get(key, []):
        if value <= threshold:
            return label
    return ""


def build_deep_context(signals: dict, macro: dict) -> str:
    """
    Build rich contextual framing for the deep analysis prompt.
    Each instrument gets current value + zone label + delta.
    """
    ma     = signals.get("macro_assets", {})
    csm    = signals.get("csm", {})
    reg_d1 = signals.get("regime_d1", {})
    reg_w1 = signals.get("regime_w1", {})
    mac    = signals.get("macro", {})

    lines = []

    # Regime header
    lines.append(
        f"MACRO REGIME: D1 {reg_d1.get('regime','—')} ({reg_d1.get('confidence','')}) | "
        f"W1 {reg_w1.get('regime','—')} ({reg_w1.get('confidence','')}) | "
        f"D1 momentum: {mac.get('label','—')} ({mac.get('confidence','')})"
    )
    lines.append("")
    lines.append("MACRO INSTRUMENTS (value, zone context, today's change):")

    def _delta_str(d: dict) -> str:
        bp  = d.get("delta_bp")
        pct = d.get("delta_pct")
        if bp  is not None: return f"Δ{bp:+.1f}bp"
        if pct is not None: return f"Δ{pct:+.1f}%"
        return ""

    INSTRUMENT_ORDER = [
        ("vix",    "VIX"),
        ("us10y",  "US 10Y"),
        ("us3m",   "US 3M"),
        ("curve",  "Yield Curve 10Y-3M"),
        ("gold",   "Gold"),
        ("dxy",    "DXY"),
        ("spx",    "S&P 500"),
        ("copper", "Copper"),
        ("wti",    "WTI Oil"),
        ("btc",    "Bitcoin"),
    ]
    for key, display in INSTRUMENT_ORDER:
        d = ma.get(key)
        if not d:
            continue
        val      = d["value"]
        zone     = _get_zone(val, key)
        unit     = "%" if key in ("us10y", "us3m") else ("bp" if key == "curve" else "")
        dir_sym  = {"up": "↑", "down": "↓"}.get(d.get("direction", ""), "→")
        delta    = _delta_str(d)
        # Format value sensibly
        if key in ("spx", "btc"):
            val_str = f"{val:,.0f}"
        elif key in ("us10y", "us3m"):
            val_str = f"{val:.2f}%"
        elif key == "curve":
            val_str = f"{val:+.1f}bp"
        else:
            val_str = f"{val:.2f}"
        zone_str = f" [{zone}]" if zone else ""
        lines.append(f"  {display}: {val_str} {dir_sym} {delta}{zone_str}")

    # CSM rankings
    csm_d1 = csm.get("d1", {})
    if csm_d1:
        sorted_csm = sorted(csm_d1.items(), key=lambda x: x[1], reverse=True)
        csm_str = " > ".join(f"{c}:{v:.0f}" for c, v in sorted_csm)
        lines.append(f"\nCSM D1 RANKING (100=strongest): {csm_str}")

    # Top setups with key metrics
    ranked = signals.get("ranked", {})
    top    = ranked.get("top", [])
    pairs  = signals.get("pairs", {})
    if top:
        lines.append("\nTOP SETUPS (pre-scored by machine):")
        for r in top[:3]:
            pd    = pairs.get(r["pair"], {})
            mom   = pd.get("mom", {})
            pills = pd.get("pills", {})
            d1p   = pills.get("d1", "—")
            cmp   = mom.get("cmp", "—")
            dd1   = mom.get("dd1") or 0
            cont  = pd.get("cont", "—")
            adx   = pd.get("adx", "—")
            arrow = "LONG" if r["direction"] == "bull" else "SHORT"
            csm_b = csm_d1.get(r["pair"][:3], 50)
            csm_q = csm_d1.get(r["pair"][3:], 50)
            csm_div = (csm_b - csm_q) if r["direction"] == "bull" else (csm_q - csm_b)
            lines.append(
                f"  {r['pair']} {arrow} [{r['score']:.1f}/10] "
                f"D1={d1p} cont={cont} CMP={cmp} MOMdelta={dd1:+.0f} "
                f"CSMdiv={csm_div:+.0f} ADX={adx}"
            )

    return "\n".join(lines)


def call_deep_analysis(signals: dict, macro: dict, headlines: list = None) -> dict:
    """
    Daily Sonnet call — headline + 180-word interpretive macro narrative.
    Returns {headline, text, generated_at} for storage in signals.json deep_analysis key.
    """
    context = build_deep_context(signals, macro)
    
    # Add news context if available
    if headlines:
        context += "\n\nRECENT NEWS (last 2 hours):\n"
        for h in headlines[:5]:
            context += f"  • {h}\n"

    system = (
        "You are a senior macro FX analyst with 20 years experience. "
        "Your role is economic interpretation, not data description. "
        "When you see a number, explain what it means mechanically for markets — "
        "the transmission channel from instrument to currency pair. "
        "Be concrete about which pairs are most affected and why. "
        "Never list data back at the reader; they can see it themselves. "
        "Respond in valid JSON only — no markdown, no backticks, no explanation outside the JSON."
    )

    prompt = (
        f"{context}\n\n"
        "Produce a JSON object with exactly two keys:\n"
        "1. \"headline\": A single sentence (max 12 words) that captures the single most "
        "important macro condition or tension right now. Make it specific and striking — "
        "not generic. Example: \"Gold-dollar divergence signals fiscal credibility crisis, "
        "not risk-off positioning.\"\n"
        "2. \"text\": A 170-180 word deep analysis in continuous prose. "
        "No headers, no bullets, no markdown. Cover in order:\n"
        "   a) The 2-3 most anomalous signals and their economic mechanism — "
        "why this value matters and what historically follows.\n"
        "   b) Whether signals tell a coherent story or contradict — name tensions explicitly.\n"
        "   c) Which specific pairs are most reinforced or challenged, and precisely why.\n"
        "Plain text for the \"text\" value. Strict 170-180 word count.\n\n"
        "Return only valid JSON. Example format: "
        "{\"headline\": \"...\", \"text\": \"...\"}"
    )

    raw = _sonnet(system, prompt, max_tokens=500)

    # Parse JSON response
    try:
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        parsed = json.loads(raw[start:end])
        headline = parsed.get('headline', '').strip()
        text     = parsed.get('text', '').strip()
        if not text:
            raise ValueError("empty text")
    except Exception:
        # Fallback: treat entire response as text, no headline
        headline = ''
        text = raw.strip()

    return {
        "headline":     headline,
        "text":         text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }




def get_breaking_headline(headlines: list) -> str:
    """Haiku identifies the single most important headline for FX traders (last 2h)."""
    if not headlines or len(headlines) < 1:
        return ""
    prompt = f"""You are a senior FX market analyst. Given these headlines from the last 2 hours, 
identify the SINGLE most important one for currency traders to know RIGHT NOW.
Return ONLY the headline itself — no explanation, no "IMPORTANT:", no numbering.
Maximum 12 words. Be specific and actionable.

Headlines:
{chr(10).join(f"• {h}" for h in headlines[:10])}"""
    result = _haiku(prompt, max_tokens=20).strip()
    return result if result and result.lower() != "no breaking news" else ""

def call_week_ahead(signals: dict, calendar_events: list[dict]) -> str:
    """
    Haiku + web search: synthesise a Week Ahead briefing from fresh articles
    plus current signals context. Runs once on Sunday ~20:00 UTC.
    """
    reg    = signals.get("regime_d1", {})
    mac    = signals.get("macro", {})
    ma     = signals.get("macro_assets", {})

    # Asset snapshot line
    asset_parts = []
    for key in ("vix", "us10y", "wti", "gold", "spx"):
        d = ma.get(key, {})
        if d.get("direction") in ("up", "down"):
            lbl  = d.get("label", key.upper())
            dir_ = "↑" if d["direction"] == "up" else "↓"
            pct  = d.get("delta_pct")
            bp   = d.get("delta_bp")
            ch   = f"{bp:+.1f}bp" if bp is not None else (f"{pct:+.1f}%" if pct is not None else "")
            asset_parts.append(f"{lbl}{dir_}{ch}")
    asset_line = " | ".join(asset_parts) if asset_parts else "—"

    # Calendar events this week
    cal_lines = []
    for e in calendar_events[:6]:
        fore = f"fcst {e['forecast']}" if e.get("forecast") else ""
        prev = f"prev {e['previous']}" if e.get("previous") else ""
        meta = "  ".join(filter(None, [fore, prev]))
        cal_lines.append(f"{e['day']} {e['time']} {e['currency']} {e['name']}"
                         + (f"  {meta}" if meta else ""))

    cal_block = "\n".join(cal_lines) if cal_lines else "No high-impact events found"

    prompt = (
        f"Search for 'FX week ahead forex' and 'forex market outlook this week' to find current weekly FX previews. "
        f"Write the briefing directly — no preamble, do not start with 'Based on' or any introduction. "
        f"Begin immediately with the dominant macro theme.\n\n"
        f"Current market state:\n"
        f"D1 Regime: {reg.get('regime','—')} {reg.get('confidence','')}\n"
        f"Macro: {mac.get('label','—')} {mac.get('confidence','')}\n"
        f"Assets: {asset_line}\n\n"
        f"High-impact events this week:\n{cal_block}\n\n"
        f"Write exactly 4 sentences covering: "
        f"(1) dominant macro theme carrying into the week, "
        f"(2) the most important data release or central bank event and which currency it affects, "
        f"(3) one specific pair or currency to watch and why, "
        f"(4) the biggest risk or wildcard for the week. "
        f"Plain text only — no markdown, no asterisks, no bullet points, no special characters. "
        f"Write as a continuous paragraph. Maximum 80 words."
    )
    return _haiku_search(prompt, max_tokens=400)


def call_catalyst(headlines: list[str], ranked_top: list[dict]) -> str:
    """
    Haiku: scan recent headlines for anything that conflicts with,
    accelerates, or invalidates the top setups. Max 25 words.
    """
    if not headlines:
        return "No recent headlines available."
    pairs_str = ", ".join(
        f"{r['pair']} {'LONG' if r['direction'] == 'bull' else 'SHORT'}"
        for r in ranked_top[:3]
    )
    h_block = "\n".join(f"- {h}" for h in headlines[:12])
    prompt = (
        f"Top setups: {pairs_str}\n\n"
        f"Headlines (last 6h):\n{h_block}\n\n"
        "Does any headline directly conflict with, accelerate, or invalidate one of these setups? "
        "Be specific — name the pair and the catalyst. "
        "Maximum 25 words. Plain text only — no markdown, no asterisks, no dashes, no special characters. "
        "If nothing material, respond only with: No breaking catalysts."
    )
    return _haiku(prompt, max_tokens=60)


def call_ranked_analysis(signals: dict) -> tuple:
    """
    Haiku: one sentence per setup, max 12 words each.
    Returns (ranked_out dict, full ranked list).
    """
    from scanner.rank import rank_pairs, build_haiku_prompt
    ranked = rank_pairs(signals)
    if not ranked:
        return {"text": "No qualifying setups.", "top": []}, []
    prompt = build_haiku_prompt(ranked, signals)
    text   = _haiku(prompt, max_tokens=120)
    top3   = [{"pair": r["pair"], "direction": r["direction"], "score": r["score"]}
              for r in ranked[:3]]
    return {"text": text, "top": top3}, ranked


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=== FX Signal Board — News Scan ===")
    now = datetime.now(timezone.utc)

    sig_path = ROOT / "data" / "signals.json"
    signals  = {}
    if sig_path.exists():
        with open(sig_path) as f:
            signals = json.load(f)

    print("\n[1/6] Fetching macro data (Yahoo Finance)…")
    macro = fetch_all_macro()

    print("\n[2/6] W1 backdrop + macro momentum…")
    prev_w1 = signals.get("regime_w1")
    prev_mac = signals.get("macro")
    w1      = compute_w1_regime(macro, prev_w1)
    mac     = compute_macro(macro, prev_mac)
    macro_assets = build_macro_assets(macro)
    session = current_session(now)
    print(f"  Session: {session}")
    print(f"  W1:    {w1['regime']} {w1['confidence']} ({'Stable' if w1['stable'] else 'Shifting'})")
    print(f"  Macro: {mac['label']} {mac['confidence']} ({'Stable' if mac['stable'] else 'Shifting'})")
    active = {k: v['direction'] for k, v in macro_assets.items() if v.get('direction') != 'flat'}
    print(f"  Assets: {active}")

    print("\n[3/6] Headlines + calendar…")
    headlines = fetch_headlines()
    
    print("\n[3a/6] Breaking headline…")
    breaking = get_breaking_headline(headlines)

    # Calendar: refresh every 4 hours — also force refresh if cache is empty
    prev_cal   = signals.get("calendar", {})
    prev_upd   = prev_cal.get("updated", "2000-01-01T00:00:00+00:00")
    try:
        cal_age_h = (now - datetime.fromisoformat(prev_upd)).total_seconds() / 3600
    except Exception:
        cal_age_h = 99
    if cal_age_h >= 4 or not prev_cal.get("events"):
        print(f"  Calendar cache {cal_age_h:.1f}h old — refreshing via web search…")
        fresh = call_calendar_search(now)
        if fresh:
            events = fresh
        else:
            # Refresh failed (rate limit etc.) — keep existing cache rather than wipe
            print(f"  ⚠ Calendar refresh failed — keeping existing cache ({len(prev_cal.get('events', []))} events)")
            events = prev_cal.get("events", [])
    else:
        print(f"  Calendar cache {cal_age_h:.1f}h old — using cached events")
        events = prev_cal.get("events", [])

    print("\n[4/6] Catalyst check + pair ranking…")
    # Run ranking first so catalyst has the top setups context
    signals["macro_assets"] = macro_assets
    ranked_out, ranked_list = call_ranked_analysis(signals)
    print(f"  Top pairs: {[r['pair'] for r in ranked_list[:3]]}")
    time.sleep(2)
    catalyst = call_catalyst(headlines, ranked_out.get("top", []))
    print(f"  Catalyst: {catalyst}")

    # ── Deep Analysis — generated once daily at 06:00 UTC ────────────────────
    is_deep_run = (now.hour == 6)
    if is_deep_run:
        print("\n[DA] Deep Analysis — daily Sonnet narrative…")
        deep = call_deep_analysis(signals, macro, headlines)
        print(f"  Deep Analysis: {deep['text'][:80]}…")
    else:
        prev_deep = signals.get("deep_analysis", {})
        deep = prev_deep if prev_deep.get("generated_at") else {}

    # ── Week Ahead — generated once on Sunday ~20:00 UTC ──────────────────────
    is_sunday_evening = (now.weekday() == 6 and 21 <= now.hour <= 22)
    if is_sunday_evening:
        print("\n[5/6] Week Ahead — generating weekly briefing via web search…")
        wa_text = call_week_ahead(signals, events)
        week_ahead = {"text": wa_text, "generated_at": now.isoformat()}
        print(f"  Week Ahead: {wa_text[:80]}…")
    else:
        # Week Ahead persists for 24h only
        prev_wa = signals.get("week_ahead", {})
        try:
            wa_age_h = (now - datetime.fromisoformat(prev_wa.get("generated_at", "2000-01-01T00:00:00+00:00"))).total_seconds() / 3600
            week_ahead = prev_wa if wa_age_h < 24 and prev_wa.get("text") else {}
        except Exception:
            week_ahead = {}

    signals["regime_w1"]    = w1
    signals["macro"]        = mac
    signals["macro_assets"] = macro_assets
    signals["catalyst"]     = {"text": catalyst, "updated": now.isoformat()}
    signals["ranked"]       = {**ranked_out, "updated": now.isoformat()}
    signals["calendar"]     = {"events": events, "updated": now.isoformat()}
    if breaking:
        signals["breaking"] = {"text": breaking, "updated": now.isoformat()}
    if deep:
        signals["deep_analysis"] = deep
    if week_ahead:
        signals["week_ahead"] = week_ahead
    else:
        signals.pop("week_ahead", None)   # explicitly clear expired week_ahead
    signals["updated"]      = now.isoformat()

    with open(sig_path, "w") as f:
        json.dump(signals, f, indent=2)
    print(f"\n✓ Saved {sig_path}")
    print("=== News Scan complete ===")


if __name__ == "__main__":
    main()
