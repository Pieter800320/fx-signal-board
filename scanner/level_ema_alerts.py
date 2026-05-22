"""
FX Signal Board — level alert + EMA touch alert checker
Called from scan_h1.py after pair scoring is complete.

Level alerts  : read data/level_alerts.json (written by dashboard via GitHub API)
EMA alerts    : always-on for all 12 pairs, no config file needed
EMA state     : read/write data/ema_alert_state.json (touch tracking, scanner-only)
"""

import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent

LEVEL_FILE = ROOT / "data" / "level_alerts.json"
EMA_STATE  = ROOT / "data" / "ema_alert_state.json"


# ── File helpers ──────────────────────────────────────────────────────────────

def _load(path, default):
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            print(f"  [alerts] load error {path.name}: {e}")
    return default


def _save(path, data):
    path.parent.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _dec(pair: str) -> int:
    return 3 if "JPY" in pair else 5


# ── Level alerts ──────────────────────────────────────────────────────────────

def check_levels(pair_prices: dict, send_telegram) -> None:
    """
    Check active horizontal levels against current H1 close prices.
    Fires Telegram and deactivates the alert on touch.

    pair_prices: { "EURUSD": float, ... }  — current price per pair
    """
    alerts = _load(LEVEL_FILE, [])
    if not alerts:
        return

    changed = False
    for a in alerts:
        if not a.get("active"):
            continue
        pair    = a.get("pair", "")
        price   = a.get("price")
        direction = a.get("direction")
        current = pair_prices.get(pair)

        if price is None or current is None or direction not in ("above", "below"):
            continue

        hit = (direction == "above" and current >= price) or \
              (direction == "below" and current <= price)

        if hit:
            a["active"] = False
            changed     = True
            d = _dec(pair)
            arrow = "↑" if direction == "above" else "↓"
            emoji = "🟢" if direction == "above" else "🔴"
            msg = (
                f"{emoji} <b>Level Alert — {pair}</b>\n"
                f"\n"
                f"Price crossed <b>{arrow} {price:.{d}f}</b>\n"
                f"Current: <b>{current:.{d}f}</b>"
            )
            print(f"  [Level] {pair}: hit {price:.{d}f} ({direction}) → Telegram")
            send_telegram(msg)

    if changed:
        _save(LEVEL_FILE, alerts)
        print(f"  [Level] level_alerts.json updated ({sum(1 for a in alerts if a.get('active'))} still active)")


# ── EMA touch alerts ──────────────────────────────────────────────────────────

def check_ema_touches(pair_prices: dict, pair_emas: dict, send_telegram) -> None:
    """
    Detect price touches of H4 EMA200/EMA50 for all 12 pairs.
    Touch = price high/low crosses the EMA (wick or body).
    Fires Telegram once per cross; re-arms on next cross in opposite direction.

    pair_prices : { "EURUSD": {"close": float, "high": float, "low": float} }
    pair_emas   : { "EURUSD": {"ema200": float, "ema50": float} }
    """
    state   = _load(EMA_STATE, {})
    changed = False

    for pair, emas in pair_emas.items():
        bar    = pair_prices.get(pair)
        if not bar or not isinstance(bar, dict):
            continue

        high    = bar.get("high")
        low     = bar.get("low")
        close   = bar.get("close")
        if high is None or low is None:
            continue

        pair_state = state.setdefault(pair, {})
        d = _dec(pair)

        for which, ema_val in [("ema200", emas.get("ema200")), ("ema50", emas.get("ema50"))]:
            if ema_val is None:
                continue

            # Touch: candle range (high/low) overlaps the EMA
            touched   = low <= ema_val <= high
            new_side  = "above" if close > ema_val else "below"
            last_side = pair_state.get(f"{which}_side")
            state_key = f"{which}_side"

            # Fire on first touch after being on one side, or on a cross
            if touched and last_side is not None and last_side == new_side:
                # Touched but didn't cross — check if we already fired this touch
                if pair_state.get(f"{which}_touched"):
                    if pair_state.get(f"{which}_side_post") == new_side:
                        continue  # already fired this touch sequence
            
            crossed = last_side is not None and last_side != new_side
            if (touched or crossed) and last_side is not None:
                if not pair_state.get(f"{which}_fired"):
                    label = "EMA 200" if which == "ema200" else "EMA 50"
                    arrow = "↑" if new_side == "above" else "↓"
                    emoji = "🟢" if new_side == "above" else "🔴"
                    msg = (
                        f"{emoji} <b>{label} Touch — {pair}</b>\n"
                        f"\n"
                        f"Price {'crossed' if crossed else 'touched'} {label} {arrow}\n"
                        f"{label}: <b>{ema_val:.{d}f}</b>  |  "
                        f"Close: <b>{close:.{d}f}</b>"
                    )
                    print(f"  [EMA] {pair}: {'crossed' if crossed else 'touched'} {label} {arrow} → Telegram")
                    send_telegram(msg)
                    pair_state[f"{which}_fired"] = True
                    changed = True

            # Reset fired flag when price moves clearly away (no longer touching)
            if not touched and not crossed:
                if pair_state.get(f"{which}_fired"):
                    pair_state[f"{which}_fired"] = False
                    changed = True

            if pair_state.get(state_key) != new_side:
                pair_state[state_key] = new_side
                changed = True

    if changed:
        _save(EMA_STATE, state)
