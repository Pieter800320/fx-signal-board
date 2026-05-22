"""
FX Signal Board — news scanner (runs 4x daily: 06/10/14/21 UTC)

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

# Yahoo Finance v8 — no API key needed
# (key, yf_symbol, label, risk_off_when_up)
MACRO_INSTRUMENTS = [
    ("vix",    "^VIX",    "VIX",      True),   # up = fear = risk-off
    ("spx",    "^GSPC",   "S&P 500",  False),  # down = risk-off
    ("gold",   "GC=F",    "Gold",     True),   # up = safe haven = risk-off
    ("dxy",    "DX-Y.NYB","DXY",      True),   # up = USD strength = risk-off
    ("copper", "HG=F",    "Copper",   False),  # down = growth fear = risk-off
    ("us10y",  "^TNX",    "US 10Y",   False),  # down = flight to safety = risk-off
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
            d["risk_off_up"] = risk_off_up
            macro[key]       = d
            pct = (d["close"] / d["prev_close"] - 1) * 100 if d["prev_close"] else 0
            print(f"    → {d['close']:.4g} ({pct:+.2f}%)")
        time.sleep(2)  # light throttle — no strict rate limit on YF
    print(f"  Macro: {len(macro)}/{len(MACRO_INSTRUMENTS)} fetched")
    return macro


# ── W1 BACKDROP ───────────────────────────────────────────────────────────────
def compute_w1_regime(macro: dict) -> dict:
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
        return {"regime": "Mixed", "confidence": "Low", "score": 5.0, "signals": 0, "total": 0}

    net = sum(scores)
    n   = len(scores)

    if net >= 3:   regime, confidence = "Risk-Off", "High"
    elif net >= 1: regime, confidence = "Risk-Off", "Medium"
    elif net <= -3:regime, confidence = "Risk-On",  "High"
    elif net <= -1:regime, confidence = "Risk-On",  "Medium"
    else:          regime, confidence = "Mixed",     "Low"

    score_val = round(5.0 + net / n * 5.0, 1)
    return {"regime": regime, "confidence": confidence,
            "score": max(0.0, min(10.0, score_val)),
            "signals": net, "total": n}


# ── SESSION DETECTION ─────────────────────────────────────────────────────────
def current_session(now: datetime) -> str:
    """Returns the active FX session based on UTC hour."""
    h = now.hour
    if 7 <= h < 8:    return "London open"
    if 8 <= h < 12:   return "London session"
    if 12 <= h < 16:  return "London/NY overlap"
    if 16 <= h < 21:  return "NY session"
    if 0 <= h < 7:    return "Asian session"
    return "Off-hours"  # 21:00–00:00 UTC


# ── MACRO MOMENTUM (daily, institutional thresholds) ──────────────────────────
def compute_macro(macro: dict) -> dict:
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

    net   = sum(scores)
    total = len(scores)  # only instruments that voted

    label      = "Risk-Off" if net > 0 else "Risk-On" if net < 0 else "Mixed"
    abs_net    = abs(net)
    confidence = "High"   if abs_net >= 3 else \
                 "Medium" if abs_net == 2 else \
                 "Low"    if abs_net == 1 else "Neutral"

    return {"label": label, "signals": net, "total": total, "confidence": confidence}


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
def fetch_calendar() -> list[str]:
    if not TWELVEDATA:
        return []
    try:
        today = datetime.now(timezone.utc)
        end   = today + timedelta(days=7)
        params = urllib.parse.urlencode({
            "start_date": today.strftime("%Y-%m-%d"),
            "end_date":   end.strftime("%Y-%m-%d"),
            "importance": "3",
            "apikey":     TWELVEDATA,
        })
        with urllib.request.urlopen(
            f"https://api.twelvedata.com/economic_calendar?{params}", timeout=10
        ) as r:
            data = json.loads(r.read().decode())
        events = data.get("result", {}).get("list", []) or data.get("events", [])
        out = []
        for ev in events[:6]:
            name    = ev.get("event") or ev.get("title") or ""
            country = ev.get("country", "")
            dt_str  = ev.get("date") or ev.get("datetime") or ""
            if name and dt_str:
                try:
                    dt    = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    label = dt.strftime("%a %H:%M UTC")
                except Exception:
                    label = dt_str[:10]
                out.append(f"{name} ({country}) — {label}")
        print(f"  Calendar events: {len(out)}")
        return out
    except Exception as e:
        print(f"  ⚠ Calendar: {e}")
        return []


# ── HAIKU CALLS ───────────────────────────────────────────────────────────────
def _haiku(prompt: str, max_tokens: int = 120) -> str:
    if not ANTHROPIC_KEY:
        return "—"
    body = json.dumps({
        "model": HAIKU_MODEL, "max_tokens": max_tokens,
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
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())["content"][0]["text"].strip()
    except Exception as e:
        return f"Unavailable ({e})"


def call_news_themes(macro: dict, headlines: list[str], events: list[str]) -> dict:
    """Haiku call 1: themes from headlines + biggest event."""
    def fmt(key, label):
        d = macro.get(key)
        if not d:
            return f"{label}: n/a"
        pct = (d["close"] / d["prev_close"] - 1) * 100 if d.get("prev_close") else 0
        return f"{label}: {d['close']:.4g} ({pct:+.2f}%)"

    macro_lines = "\n".join([
        fmt("vix", "VIX"), fmt("spx", "S&P500"),
        fmt("gold", "Gold"), fmt("dxy", "DXY"),
        fmt("copper", "Copper"), fmt("us10y", "US10Y"),
    ])
    h_block = "\n".join(f"- {h}" for h in headlines) if headlines else "No headlines."
    e_block = "\n".join(f"- {e}" for e in events[:4]) if events else "No events."

    prompt = (
        f"Cross-asset (daily change):\n{macro_lines}\n\n"
        f"FX headlines:\n{h_block}\n\n"
        f"Upcoming high-impact events:\n{e_block}\n\n"
        "Output exactly 2 lines — no labels, no markdown:\n"
        "Line 1: 1-2 dominant macro themes driving FX. Max 20 words. Be specific.\n"
        "Line 2: Most important upcoming event and expected FX impact. Max 20 words."
    )
    text  = _haiku(prompt, max_tokens=120)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return {"themes": lines[0] if lines else "—",
            "event":  lines[1] if len(lines) > 1 else "—"}


def call_data_analysis(signals: dict, session: str, now: datetime) -> str:
    """Haiku call 2: identify key tension in signals.json data, session-aware."""
    pairs   = signals.get("pairs", {})
    d1_reg  = signals.get("regime_d1", {}).get("regime", "Unknown")
    h4_reg  = signals.get("regime_h4", {}).get("regime", "Unknown")
    h1_reg  = signals.get("regime_h1", {}).get("regime", "Unknown")
    w1_reg  = signals.get("regime_w1", {}).get("regime", "Unknown")
    mac     = signals.get("macro", {})
    mac_lbl = mac.get("label", "Unknown")
    mac_conf= mac.get("confidence", "")
    csm_d1  = signals.get("csm", {}).get("d1", {})
    csm_h4  = signals.get("csm", {}).get("h4", {})
    prev_analysis = (signals.get("analysis") or {}).get("text", "")

    pills_summary = []
    for pair, p in pairs.items():
        pills = p.get("pills", {})
        d1p   = pills.get("d1", "neutral")
        h4p   = pills.get("h4", "neutral")
        cont  = p.get("cont", 0)
        if d1p != "neutral" or h4p != "neutral":
            pills_summary.append(f"{pair}: D1={d1p} H4={h4p} Cont={cont}%")

    d1s = " ".join(f"{c}={v}" for c, v in sorted(csm_d1.items(), key=lambda x:-x[1])[:4])
    h4s = " ".join(f"{c}={v}" for c, v in sorted(csm_h4.items(), key=lambda x:-x[1])[:4])

    prev_line = f"Previous analysis: {prev_analysis}" if prev_analysis else "No previous analysis."

    prompt = (
        f"Session: {session} | Time: {now.strftime('%H:%M')} UTC\n"
        f"Regime — W1: {w1_reg} | D1: {d1_reg} | H4: {h4_reg} | H1: {h1_reg}\n"
        f"Macro D1: {mac_lbl} {mac_conf}\n"
        f"CSM D1 top: {d1s}\nCSM H4 top: {h4s}\n"
        f"Pairs:\n" + "\n".join(pills_summary[:8]) + "\n\n"
        f"{prev_line}\n\n"
        "Identify the most significant tension or confluence in this data. "
        "Note if anything has changed since the previous analysis. "
        "Name 1-2 specific pairs if relevant. "
        "1 sentence, max 30 words, plain text."
    )
    return _haiku(prompt, max_tokens=80)


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=== FX Signal Board — News Scan ===")
    now = datetime.now(timezone.utc)

    sig_path = ROOT / "data" / "signals.json"
    signals  = {}
    if sig_path.exists():
        with open(sig_path) as f:
            signals = json.load(f)

    print("\n[1/4] Fetching macro data (Yahoo Finance)…")
    macro = fetch_all_macro()

    print("\n[2/4] W1 backdrop + macro momentum…")
    w1      = compute_w1_regime(macro)
    mac     = compute_macro(macro)
    session = current_session(now)
    print(f"  Session: {session}")
    print(f"  W1:    {w1['regime']} {w1['confidence']} ({w1['signals']:+d}/{w1['total']})")
    print(f"  Macro: {mac['label']} {mac['confidence']} ({mac['signals']:+d}/{mac['total']})")

    print("\n[3/4] Headlines + calendar…")
    headlines = fetch_headlines()
    events    = fetch_calendar()

    print("\n[4/4] Claude Haiku…")
    news_out  = call_news_themes(macro, headlines, events)
    print(f"  Themes: {news_out['themes']}")
    print(f"  Event:  {news_out['event']}")
    time.sleep(3)
    analysis = call_data_analysis(signals, session, now)
    print(f"  Analysis: {analysis}")

    signals["regime_w1"] = w1
    signals["macro"]     = mac
    signals["news"]      = {"themes": news_out["themes"],
                            "event":  news_out["event"],
                            "updated": now.isoformat()}
    signals["analysis"]  = {"text": analysis, "updated": now.isoformat()}
    signals["updated"]   = now.isoformat()

    with open(sig_path, "w") as f:
        json.dump(signals, f, indent=2)
    print(f"\n✓ Saved {sig_path}")
    print("=== News Scan complete ===")


if __name__ == "__main__":
    main()
