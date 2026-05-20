"""
FX Signal Board — full scan (runs every 4 hours)

Fetches all TF OHLCV, computes pills / MOM / CSM / regime / ADX / cont.
Writes data/signals.json.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scanner.config    import PAIRS, CORRELATES
from scanner.fetch     import fetch_all_pairs
from scanner.pills     import classify_all
from scanner.mom1212   import compute_all as compute_mom
from scanner.csm       import compute_csm
from scanner.regime    import classify_regime, regime_block_cls
from scanner.cont_score import compute_cont


def load_prev_signals() -> dict:
    path = ROOT / "data" / "signals.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def adx14(df) -> float | None:
    """Compute ADX(14) from H4 OHLCV."""
    import numpy as np
    if df is None or len(df) < 28:
        return None
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)

    plus_dm  = np.where(h[1:] - h[:-1] > l[:-1] - l[1:],
                        np.maximum(h[1:] - h[:-1], 0), 0)
    minus_dm = np.where(l[:-1] - l[1:] > h[1:] - h[:-1],
                        np.maximum(l[:-1] - l[1:], 0), 0)

    tr = np.maximum(h[1:] - l[1:],
         np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))

    period = 14
    atr14    = np.mean(tr[-period:])
    pdm14    = np.mean(plus_dm[-period:])
    mdm14    = np.mean(minus_dm[-period:])

    if atr14 == 0:
        return 0.0

    pdi = 100 * pdm14 / atr14
    mdi = 100 * mdm14 / atr14
    dx  = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0

    # Rough ADX from last 14 DX values
    tr_arr   = tr[-period*2:]
    pdm_arr  = plus_dm[-period*2:]
    mdm_arr  = minus_dm[-period*2:]
    dx_vals  = []
    for i in range(period - 1, len(tr_arr)):
        a  = tr_arr[i-period+1:i+1].mean()
        pd = pdm_arr[i-period+1:i+1].mean()
        md = mdm_arr[i-period+1:i+1].mean()
        if a > 0:
            pi = 100 * pd / a
            mi = 100 * md / a
            d  = 100 * abs(pi - mi) / (pi + mi) if (pi + mi) > 0 else 0
            dx_vals.append(d)
    if not dx_vals:
        return round(dx, 1)
    return round(float(np.mean(dx_vals[-period:])), 1)


def d_pct(df, bars_back: int) -> float | None:
    if df is None or len(df) < bars_back + 1:
        return None
    prev  = float(df["close"].iloc[-(bars_back + 1)])
    close = float(df["close"].iloc[-1])
    if prev == 0:
        return None
    return round((close / prev - 1) * 100, 2)


def main():
    print("=== FX Signal Board — Full Scan ===")
    now = datetime.now(timezone.utc)

    prev = load_prev_signals()
    prev_h4_regime = prev.get("regime_h4")

    # ── 1. Fetch all TFs ───────────────────────────────────────────────────────
    print("\n[1/5] Fetching OHLCV (W1 / D1 / H4 / H1) for 12 pairs…")
    ohlcv = fetch_all_pairs(["w1", "d1", "h4", "h1"])

    # ── 2. Pills ───────────────────────────────────────────────────────────────
    print("\n[2/5] Classifying pills…")
    pair_pills = {}
    for pair_key, tfs in ohlcv.items():
        pair_pills[pair_key] = classify_all(tfs)
        print(f"  {pair_key}: {pair_pills[pair_key]}")

    # ── 3. MOM + ADX ──────────────────────────────────────────────────────────
    print("\n[3/5] Computing MOM 1212 + ADX…")
    pair_mom = {}
    pair_adx = {}
    for pair_key, tfs in ohlcv.items():
        pair_mom[pair_key] = compute_mom(tfs)
        pair_adx[pair_key] = adx14(tfs.get("h4"))
        print(f"  {pair_key}: MOM CMP={pair_mom[pair_key].get('cmp')} ADX={pair_adx[pair_key]}")

    # ── 4. CSM ────────────────────────────────────────────────────────────────
    print("\n[4/5] Computing CSM…")
    csm = compute_csm(ohlcv)
    print(f"  D1: {dict(sorted(csm['d1'].items(), key=lambda x:-x[1]))}")
    print(f"  H4: {dict(sorted(csm['h4'].items(), key=lambda x:-x[1]))}")

    # ── 5. Regime + Cont + assemble ───────────────────────────────────────────
    print("\n[5/5] Regime, Cont. score, assembling…")
    regime_h4 = classify_regime(csm["h4"], pair_pills, prev_h4_regime)
    print(f"  H4 Regime: {regime_h4['regime']} {regime_h4['confidence']}")

    pairs_out = {}
    for pair in PAIRS:
        key = pair.replace("/", "")
        pills = pair_pills.get(key, {})
        mom   = pair_mom.get(key, {})
        adx   = pair_adx.get(key)
        cont  = compute_cont(key, pills, adx, csm["d1"], regime_h4)
        cls   = regime_block_cls(key, pills, regime_h4)
        d1p   = d_pct(ohlcv.get(key, {}).get("d1"), 1)
        d5p   = d_pct(ohlcv.get(key, {}).get("d1"), 5)

        pairs_out[key] = {
            "pills":      pills,
            "mom":        mom,
            "adx":        adx,
            "d1_pct":     d1p,
            "d5_pct":     d5p,
            "cont":       cont,
            "regime_cls": cls,
        }
        print(f"  {key}: cont={cont}% cls={cls}")

    # ── Write signals.json ────────────────────────────────────────────────────
    # Preserve news/W1/macro from previous context if present
    prev_context = {
        k: prev.get(k)
        for k in ("regime_w1", "macro", "news")
        if prev.get(k)
    }

    out = {
        "updated":    now.isoformat(),
        "regime_h4":  regime_h4,
        "csm":        csm,
        "pairs":      pairs_out,
        **prev_context,
    }

    path = ROOT / "data" / "signals.json"
    path.parent.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n✓ Saved {path}")
    print("=== Full Scan complete ===")


if __name__ == "__main__":
    main()
