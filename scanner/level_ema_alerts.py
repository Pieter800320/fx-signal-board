"""
FX Signal Board — level alert + EMA touch alert checker
Called from scan_h1.py after pair scoring is complete.

Level alerts  : read data/level_alerts.json (written by dashboard via GitHub API)
EMA alerts    : read data/ema_alerts.json   (enabled pairs/EMAs, written by dashboard)
EMA state     : read/write data/ema_alert_state.json (price-side tracking, scanner-only)
"""

import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent

LEVEL_FILE = ROOT / "data" / "level_alerts.json"
EMA_FILE   = ROOT / "data" / "ema_alerts.json"
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
    Detect price crosses of EMA200/50 (H4) for pairs where alerts are enabled.
    Fires Telegram once per cross; re-arms on the next cross in the opposite direction.

    pair_prices : { "EURUSD": float }         — current H1 close per pair
    pair_emas   : { "EURUSD": {"ema200": float, "ema50": float} }  — H4 EMA values
    """
    settings = _load(EMA_FILE, {})
    if not settings:
        return

    state   = _load(EMA_STATE, {})
    changed = False

    for pair, cfg in settings.items():
        ema200_on = cfg.get("ema200", False)
        ema50_on  = cfg.get("ema50",  False)
        if not ema200_on and not ema50_on:
            continue

        current = pair_prices.get(pair)
        emas    = pair_emas.get(pair, {})
        if current is None:
            continue

        pair_state = state.setdefault(pair, {})
        d = _dec(pair)

        for which, enabled, ema_val in [
            ("ema200", ema200_on, emas.get("ema200")),
            ("ema50",  ema50_on,  emas.get("ema50")),
        ]:
            if not enabled or ema_val is None:
                continue

            new_side  = "above" if current > ema_val else "below"
            last_side = pair_state.get(f"{which}_side")
            state_key = f"{which}_side"

            if last_side is not None and last_side != new_side:
                label = "EMA 200" if which == "ema200" else "EMA 50"
                arrow = "↑" if new_side == "above" else "↓"
                emoji = "🟢" if new_side == "above" else "🔴"
                msg = (
                    f"{emoji} <b>{label} Touch — {pair}</b>\n"
                    f"\n"
                    f"Price crossed {label} {arrow}\n"
                    f"{label}: <b>{ema_val:.{d}f}</b>  |  "
                    f"Price: <b>{current:.{d}f}</b>"
                )
                print(f"  [EMA] {pair}: crossed {label} {arrow} → Telegram")
                send_telegram(msg)

            if pair_state.get(state_key) != new_side:
                pair_state[state_key] = new_side
                changed = True

    if changed:
        _save(EMA_STATE, state)
