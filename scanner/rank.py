"""
FX Signal Board — pair ranking engine
Deterministic scoring before Haiku narrative call.

Six components (weighted):
  cont      25% — continuation score gate + quality
  cmp       20% — CMP alignment with direction (>60 bull / <40 bear)
  mom_dlt   15% — D1 MOM delta direction and magnitude
  csm       20% — D1 CSM divergence base vs quote
  regime    10% — D1 regime supports direction
  cross     10% — cross-asset macro tailwinds

Hard gate: D1 pill must be directional AND cont >= 45.
"""

# ── Cross-asset currency impact table ─────────────────────────────────────────
# (asset, direction) -> {currency: impact}
# Positive = strengthens currency, negative = weakens currency
# Magnitude: 1=weak, 2=strong, 3=very strong
CROSS_ASSET = {
    ("vix",    "up"):    {"JPY": 2, "CHF": 2, "USD": 1},
    ("vix",    "down"):  {"AUD": 2, "NZD": 1, "CAD": 1},
    ("us10y",  "up"):    {"USD": 2, "JPY": -2},
    ("us10y",  "down"):  {"JPY": 2, "CHF": 1},
    ("wti",    "up"):    {"CAD": 3},
    ("wti",    "down"):  {"CAD": -3},
    ("gold",   "up"):    {"CHF": 2, "JPY": 1, "AUD": 1},
    ("gold",   "down"):  {"USD": 1},
    ("spx",    "up"):    {"AUD": 2, "NZD": 1, "CAD": 1, "GBP": 1},
    ("spx",    "down"):  {"JPY": 2, "CHF": 1},
    ("copper", "up"):    {"AUD": 2, "NZD": 1},
    ("copper", "down"):  {"AUD": -1},
}

RISK_OFF_CCYS = {"JPY", "CHF", "USD"}
RISK_ON_CCYS  = {"AUD", "NZD", "CAD", "GBP", "EUR"}

WEIGHTS = {
    "cont":    0.25,
    "cmp":     0.20,
    "mom_dlt": 0.15,
    "csm":     0.20,
    "regime":  0.10,
    "cross":   0.10,
}


# ── Component scorers (each returns 0–10) ─────────────────────────────────────

def _pair_direction(pair_data):
    d1 = pair_data.get("pills", {}).get("d1", "neutral")
    if d1 in ("bull", "bull_strong"): return "bull"
    if d1 in ("bear", "bear_strong"): return "bear"
    return None


def _cont_sc(cont):
    return round(max(0.0, min(10.0, (cont - 45) / 55 * 10)), 1)


def _cmp_sc(cmp, direction):
    if cmp is None:
        return 5
    if direction == "bull":
        if cmp >= 65: return 10
        if cmp >= 55: return 6
        if cmp >= 45: return 3
        return 0
    else:
        if cmp <= 35: return 10
        if cmp <= 45: return 6
        if cmp <= 55: return 3
        return 0


def _mom_dlt_sc(dd1, direction):
    if dd1 is None:
        return 5
    aligned = (direction == "bull" and dd1 > 0) or (direction == "bear" and dd1 < 0)
    if not aligned:
        return 0
    a = abs(dd1)
    if a >= 25: return 10
    if a >= 10: return 7
    return 4


def _csm_sc(pair, csm_d1, direction):
    base  = pair[:3]
    quote = pair[3:]
    bv = csm_d1.get(base,  50)
    qv = csm_d1.get(quote, 50)
    div = (bv - qv) if direction == "bull" else (qv - bv)
    if div >= 50: return 10
    if div >= 30: return 7
    if div >= 15: return 5
    if div >= 0:  return 3
    return 1


def _regime_sc(pair, direction, regime_d1):
    regime = (regime_d1 or {}).get("regime", "Mixed")
    conf   = (regime_d1 or {}).get("confidence", "Low")

    if regime == "Risk-Off":
        favoured_bull = RISK_OFF_CCYS
        favoured_bear = RISK_ON_CCYS
    elif regime == "Risk-On":
        favoured_bull = RISK_ON_CCYS
        favoured_bear = RISK_OFF_CCYS
    else:
        return 5  # Ranging / Mixed — no regime edge

    base  = pair[:3]
    quote = pair[3:]
    if direction == "bull":
        base_fits  = base  in favoured_bull
        quote_fits = quote in favoured_bear
    else:
        base_fits  = base  in favoured_bear
        quote_fits = quote in favoured_bull

    conf_mult = {"High": 1.0, "Medium": 0.8}.get(conf, 0.6)

    if base_fits and quote_fits: raw = 10
    elif base_fits or quote_fits: raw = 7
    else: raw = 2
    return round(raw * conf_mult)


def _cross_sc(pair, direction, macro_assets):
    """
    Score 0–10 based on cross-asset tailwinds.
    Also returns list of short signal strings for the prompt.
    """
    if not macro_assets:
        return 5, []

    base  = pair[:3]
    quote = pair[3:]
    total   = 0
    signals = []

    for (asset, asset_dir), impacts in CROSS_ASSET.items():
        data = macro_assets.get(asset, {})
        if data.get("direction") != asset_dir:
            continue

        bi = impacts.get(base,  0)
        qi = impacts.get(quote, 0)

        # Support = base strengthens (bull) or quote weakens (bull),
        #           base weakens (bear) or quote strengthens (bear)
        if direction == "bull":
            support = max(0, bi) + max(0, -qi)
        else:
            support = max(0, -bi) + max(0, qi)

        if support <= 0:
            continue

        label    = data.get("label", asset.upper())
        dir_word = "rising" if asset_dir == "up" else "falling"
        if direction == "bull" and bi > 0:
            signals.append(f"{label} {dir_word} → {base} bid")
        elif direction == "bull" and qi < 0:
            signals.append(f"{label} {dir_word} → {quote} offered")
        elif direction == "bear" and bi < 0:
            signals.append(f"{label} {dir_word} → {base} offered")
        elif direction == "bear" and qi > 0:
            signals.append(f"{label} {dir_word} → {quote} bid")
        total += support

    # Map raw total to 0–10 (max realistic ~5 for e.g. USDCAD with WTI+VIX)
    if total >= 4: mapped = 10
    elif total == 3: mapped = 8
    elif total == 2: mapped = 6
    elif total == 1: mapped = 4
    else: mapped = 2

    return mapped, signals[:3]


# ── Main ranking functions ─────────────────────────────────────────────────────

def score_pair(pair, pair_data, csm_d1, regime_d1, macro_assets):
    """Score one pair. Returns full result dict or None if gated out."""
    direction = _pair_direction(pair_data)
    if not direction:
        return None

    cont = pair_data.get("cont", 0)
    if cont < 45:
        return None

    mom  = pair_data.get("mom", {})
    cmp  = mom.get("cmp")
    dd1  = mom.get("dd1")

    cross_sc, cross_signals = _cross_sc(pair, direction, macro_assets)

    scores = {
        "cont":    _cont_sc(cont),
        "cmp":     _cmp_sc(cmp, direction),
        "mom_dlt": _mom_dlt_sc(dd1, direction),
        "csm":     _csm_sc(pair, csm_d1, direction),
        "regime":  _regime_sc(pair, direction, regime_d1),
        "cross":   cross_sc,
    }
    weighted = round(sum(scores[k] * WEIGHTS[k] for k in WEIGHTS), 2)

    base  = pair[:3]
    quote = pair[3:]
    return {
        "pair":          pair,
        "direction":     direction,
        "score":         weighted,
        "scores":        scores,
        "cont":          cont,
        "cmp":           cmp,
        "dd1":           dd1,
        "adx":           pair_data.get("adx"),
        "cross_signals": cross_signals,
        "csm_base":      csm_d1.get(base,  50),
        "csm_quote":     csm_d1.get(quote, 50),
        "regime":        (regime_d1 or {}).get("regime", "Mixed"),
    }


def rank_pairs(signals):
    """Score and rank all pairs. Returns list sorted by score descending."""
    pairs        = signals.get("pairs", {})
    csm_d1       = signals.get("csm", {}).get("d1", {})
    regime_d1    = signals.get("regime_d1", {})
    macro_assets = signals.get("macro_assets", {})

    results = []
    for pair, pair_data in pairs.items():
        r = score_pair(pair, pair_data, csm_d1, regime_d1, macro_assets)
        if r:
            results.append(r)
    return sorted(results, key=lambda x: x["score"], reverse=True)


def build_haiku_prompt(ranked, signals):
    """Build the Haiku prompt for the ranked narrative call."""
    mac      = signals.get("macro", {})
    reg_d1   = signals.get("regime_d1", {})
    d1_reg   = reg_d1.get("regime", "—")
    d1_conf  = reg_d1.get("confidence", "")
    mac_lbl  = mac.get("label", "—")
    mac_conf = mac.get("confidence", "")

    # Asset context line
    ma = signals.get("macro_assets", {})
    asset_parts = []
    for key in ("vix", "us10y", "wti", "gold", "spx", "copper"):
        d = ma.get(key, {})
        if d.get("direction") in ("up", "down"):
            lbl  = d.get("label", key.upper())
            val  = d.get("value", "")
            pct  = d.get("delta_pct")
            bp   = d.get("delta_bp")
            ch   = f"{bp:+.1f}bp" if bp is not None else (f"{pct:+.1f}%" if pct is not None else "")
            dir_ = "↑" if d["direction"] == "up" else "↓"
            asset_parts.append(f"{lbl}{dir_}{ch}")
    asset_line = " | ".join(asset_parts) if asset_parts else "—"

    # Pair lines
    top = ranked[:3]
    pair_lines = []
    for i, r in enumerate(top, 1):
        arrow   = "LONG" if r["direction"] == "bull" else "SHORT"
        csm_div = r["csm_base"] - r["csm_quote"] if r["direction"] == "bull" \
                  else r["csm_quote"] - r["csm_base"]
        cmp_str = f"{r['cmp']}({'strong' if r['cmp'] is not None and ((r['direction']=='bull' and r['cmp']>=60) or (r['direction']=='bear' and r['cmp']<=40)) else 'weak'})"
        dd1_str = f"{'↑' if (r['dd1'] or 0) > 0 else '↓'}{abs(r['dd1'] or 0)}"
        cross   = ", ".join(r["cross_signals"]) if r["cross_signals"] else "none"
        adx     = r["adx"]
        pair_lines.append(
            f"{i}. {r['pair']} {arrow} [score {r['score']:.1f}/10] "
            f"cont={r['cont']} CMP={cmp_str} MOM-d={dd1_str} "
            f"CSM-div={csm_div:+.0f} ADX={adx} regime={r['regime']} "
            f"| cross-asset: {cross}"
        )

    return (
        f"D1 Regime: {d1_reg} {d1_conf} | Macro: {mac_lbl} {mac_conf}\n"
        f"Assets today: {asset_line}\n\n"
        "PYTHON PRE-SCORED PAIRS:\n"
        + "\n".join(pair_lines) + "\n\n"
        "For each pair write exactly ONE sentence (max 12 words): does the macro context "
        "confirm or challenge the setup? Name the specific driver. "
        "Plain text only — no markdown, no asterisks, no dashes, no bullet points, no special characters."
    )
