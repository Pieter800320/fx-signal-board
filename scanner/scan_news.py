"""
FX Signal Board — news scanner (runs 4x daily: 06/10/14/21 UTC)

1. Fetch cross-asset forex data (6 pairs) → W1 backdrop + macro momentum
2. Fetch RSS headlines (FXStreet / Reuters / Yahoo Finance)
3. Fetch Twelvedata economic calendar (high-impact events, next 7 days)
4. Haiku call 1 → News bar: dominant themes (line 1) + biggest event (line 2)
5. Haiku call 2 → Analysis bar: data tension / conflict from signals.json
6. Patch signals.json with regime_w1, macro, news, analysis
"""
import json, os, sys, time, xml.etree.ElementTree as ET, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scanner.fetch import fetch_cross_asset

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TWELVEDATA    = os.environ.get("TWELVEDATA_KEY", "")
HAIKU_MODEL   = "claude-haiku-4-5-20251001"


# ── W1 BACKDROP ───────────────────────────────────────────────────────────────
def compute_w1_regime(cross: dict) -> dict:
    signals = []

    def safe_chg(name, direction="up"):
        d = cross.get(name)
        if not d:
            return 0
        prev = d.get("w1_close", d.get("prev_close"))
        if not prev or prev == 0:
            return 0
        pct = (d["close"] / prev - 1) * 100
        return 1 if (pct > 0) == (direction == "up") else -1

    signals.append(safe_chg("GOLD",   "up"))
    signals.append(safe_chg("RISK1",  "down"))
    signals.append(safe_chg("RISK2",  "down"))
    signals.append(safe_chg("USD",    "down"))
    signals.append(safe_chg("SAFE",   "down"))
    signals.append(safe_chg("GROWTH", "down"))

    net = sum(signals)
    ro_count  = signals.count(1)
    ron_count = signals.count(-1)

    if net >= 3:   regime, confidence = "Risk-Off", "High"
    elif net >= 1: regime, confidence = "Risk-Off", "Medium"
    elif net <= -3:regime, confidence = "Risk-On",  "High"
    elif net <= -1:regime, confidence = "Risk-On",  "Medium"
    else:          regime, confidence = "Mixed",     "Low"

    score = round(5.0 + net * (5.0 / max(len(signals), 1)), 1)
    return {"regime": regime, "confidence": confidence,
            "score": max(0.0, min(10.0, score)),
            "signals": net, "total": len(signals)}


# ── MACRO MOMENTUM ────────────────────────────────────────────────────────────
def compute_macro(cross: dict) -> dict:
    signals = []

    def daily_chg(name, direction="up"):
        d = cross.get(name)
        if not d:
            return 0
        prev = d.get("prev_close")
        if not prev or prev == 0:
            return 0
        pct = (d["close"] / prev - 1) * 100
        return 1 if (pct > 0) == (direction == "up") else -1

    signals.append(daily_chg("GOLD",   "up"))
    signals.append(daily_chg("RISK1",  "down"))
    signals.append(daily_chg("RISK2",  "down"))
    signals.append(daily_chg("USD",    "down"))
    signals.append(daily_chg("SAFE",   "down"))
    signals.append(daily_chg("GROWTH", "down"))

    net = sum(signals)
    if net >= 1:   label = "Risk-Off"
    elif net <= -1:label = "Risk-On"
    else:          label = "Mixed"

    return {"label": label, "signals": net, "total": len(signals)}


# ── RSS HEADLINES ─────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://www.forexlive.com/feed/news",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=EURUSD%3DX&region=US&lang=en-US",
]

def fetch_headlines(max_per_feed: int = 6, max_age_hours: int = 8) -> list[str]:
    """Fetch RSS headlines from multiple sources. Returns plain text list."""
    headlines = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    for url in RSS_FEEDS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 FX-Signal-Board/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read().decode("utf-8", errors="replace")
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
            print(f"  ⚠ RSS {url[:40]}…: {e}")

    # Deduplicate while preserving order
    seen, unique = set(), []
    for h in headlines:
        key = h.lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(h)

    print(f"  Headlines fetched: {len(unique)}")
    return unique[:15]


# ── ECONOMIC CALENDAR ─────────────────────────────────────────────────────────
def fetch_calendar() -> list[str]:
    """
    Fetch high-impact economic events for next 7 days via Twelvedata.
    Returns list of formatted strings: "Fed Meeting Thu 19:00 UTC"
    """
    if not TWELVEDATA:
        return []
    try:
        today  = datetime.now(timezone.utc)
        end    = today + timedelta(days=7)
        params = urllib.parse.urlencode({
            "start_date": today.strftime("%Y-%m-%d"),
            "end_date":   end.strftime("%Y-%m-%d"),
            "importance": "3",          # high impact only
            "apikey":     TWELVEDATA,
        })
        url = f"https://api.twelvedata.com/economic_calendar?{params}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())

        events = data.get("result", {}).get("list", []) or data.get("events", [])
        out = []
        for ev in events[:8]:
            name    = ev.get("event") or ev.get("title") or ""
            country = ev.get("country", "")
            dt_str  = ev.get("date") or ev.get("datetime") or ""
            if name and dt_str:
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    label = dt.strftime("%a %H:%M UTC")
                except Exception:
                    label = dt_str[:10]
                out.append(f"{name} ({country}) — {label}")

        print(f"  Calendar events: {len(out)}")
        return out
    except Exception as e:
        print(f"  ⚠ Calendar: {e}")
        return []


# ── HAIKU CALL ────────────────────────────────────────────────────────────────
def _haiku(prompt: str, max_tokens: int = 120) -> str:
    if not ANTHROPIC_KEY:
        return "—"
    body = json.dumps({
        "model":      HAIKU_MODEL,
        "max_tokens": max_tokens,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            resp = json.loads(r.read().decode())
            return resp["content"][0]["text"].strip()
    except Exception as e:
        return f"Unavailable ({e})"


def call_news_themes(headlines: list[str], events: list[str]) -> dict:
    """
    Haiku call 1: extract dominant themes from headlines + biggest upcoming event.
    Returns {"themes": str, "event": str}
    """
    h_block = "\n".join(f"- {h}" for h in headlines) if headlines else "No headlines available."
    e_block = "\n".join(f"- {e}" for e in events[:4]) if events else "No events available."

    prompt = (
        f"FX News Headlines (last 8h):\n{h_block}\n\n"
        f"Upcoming high-impact events:\n{e_block}\n\n"
        "Output exactly 2 lines — no labels, no markdown:\n"
        "Line 1: The 1-2 dominant macro themes from headlines driving FX now. Max 20 words.\n"
        "Line 2: The single most important upcoming event and its expected market impact. Max 20 words.\n"
        "Be specific. Name currencies and drivers."
    )

    text = _haiku(prompt, max_tokens=120)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return {
        "themes": lines[0] if len(lines) > 0 else "—",
        "event":  lines[1] if len(lines) > 1 else "—",
    }


def call_data_analysis(signals: dict) -> str:
    """
    Haiku call 2: read signals.json, identify the key tension or conflict.
    Returns a single sentence ≤25 words.
    """
    pairs  = signals.get("pairs", {})
    h4_reg = signals.get("regime_h4", {}).get("regime", "Unknown")
    w1_reg = signals.get("regime_w1", {}).get("regime", "Unknown")
    mac    = signals.get("macro", {}).get("label", "Unknown")
    csm_d1 = signals.get("csm", {}).get("d1", {})
    csm_h4 = signals.get("csm", {}).get("h4", {})

    # Summarise pills for prompt
    pill_summary = []
    for pair, p in pairs.items():
        pills = p.get("pills", {})
        d1p = pills.get("d1", "neutral")
        h4p = pills.get("h4", "neutral")
        cont = p.get("cont", 0)
        if d1p != "neutral" or h4p != "neutral":
            pill_summary.append(f"{pair}: D1={d1p} H4={h4p} Cont={cont}%")

    # Sort CSM
    d1_sorted = sorted(csm_d1.items(), key=lambda x: -x[1])
    h4_sorted = sorted(csm_h4.items(), key=lambda x: -x[1])
    d1_str = " ".join(f"{c}={v}" for c, v in d1_sorted[:4])
    h4_str = " ".join(f"{c}={v}" for c, v in h4_sorted[:4])

    prompt = (
        f"Market snapshot:\n"
        f"W1 regime: {w1_reg} | H4 regime: {h4_reg} | Macro momentum: {mac}\n"
        f"CSM D1 (strongest→): {d1_str}\n"
        f"CSM H4 (strongest→): {h4_str}\n"
        f"Pairs: {chr(10).join(pill_summary[:8])}\n\n"
        "Identify the most significant tension, conflict or opportunity in this data.\n"
        "Name 1-2 specific pairs if relevant.\n"
        "Output: exactly 1 sentence, max 25 words, plain text, no labels."
    )

    return _haiku(prompt, max_tokens=60)


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=== FX Signal Board — News Scan ===")
    now = datetime.now(timezone.utc)

    sig_path = ROOT / "data" / "signals.json"
    signals  = {}
    if sig_path.exists():
        with open(sig_path) as f:
            signals = json.load(f)

    # ── 1. Cross-asset forex data ──────────────────────────────────────────────
    print("\n[1/4] Fetching cross-asset forex data…")
    cross = fetch_cross_asset()

    # ── 2. Regime calculations ────────────────────────────────────────────────
    print("\n[2/4] W1 backdrop + macro momentum…")
    w1  = compute_w1_regime(cross)
    mac = compute_macro(cross)
    print(f"  W1:    {w1['regime']} {w1['confidence']} ({w1['signals']:+d}/{w1['total']})")
    print(f"  Macro: {mac['label']} ({mac['signals']:+d}/{mac['total']})")

    # ── 3. RSS headlines + calendar ───────────────────────────────────────────
    print("\n[3/4] Fetching headlines + calendar…")
    headlines = fetch_headlines()
    events    = fetch_calendar()

    # ── 4. Two Haiku calls ────────────────────────────────────────────────────
    print("\n[4/4] Claude Haiku: news themes + data analysis…")
    news_out  = call_news_themes(headlines, events)
    print(f"  Themes: {news_out['themes']}")
    print(f"  Event:  {news_out['event']}")

    # Brief pause between API calls
    time.sleep(3)

    analysis = call_data_analysis(signals)
    print(f"  Analysis: {analysis}")

    # ── Patch signals.json ────────────────────────────────────────────────────
    signals["regime_w1"] = w1
    signals["macro"]     = mac
    signals["news"]      = {
        "themes":  news_out["themes"],
        "event":   news_out["event"],
        "updated": now.isoformat(),
    }
    signals["analysis"] = {
        "text":    analysis,
        "updated": now.isoformat(),
    }
    signals["updated"] = now.isoformat()

    with open(sig_path, "w") as f:
        json.dump(signals, f, indent=2)
    print(f"\n✓ Saved {sig_path}")
    print("=== News Scan complete ===")


if __name__ == "__main__":
    main()
