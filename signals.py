"""
signals.py — Entry signal detection and trade plan generation.
Classifies each stock as breakout, pullback, reversal, or wait.
Generates entry zone, confirmation, invalidation, and exit plan.
"""

import logging

log = logging.getLogger(__name__)

SETUP_BREAKOUT  = "breakout"
SETUP_PULLBACK  = "pullback"
SETUP_REVERSAL  = "reversal"
SETUP_WAIT      = "wait"


def detect_setup(tech: dict) -> str:
    """
    Classify the entry setup type based on technical structure.
    Priority: pullback > breakout > reversal > wait
    """
    cp  = tech["current_price"]
    rsi = tech["rsi"]
    trend = tech["trend"]
    resistances = tech["resistances"]
    supports    = tech["supports"]
    vol_pct     = tech["vol_pct"]

    # ── PULLBACK ──────────────────────────────────────────────────────────────
    # Price in uptrend, pulled back to support or EMA, RSI recovering
    if trend in ("Strong Bullish", "Bullish", "Cautiously Bullish"):
        at_support = supports and abs(cp - supports[0]) / cp < 0.03
        at_ema     = abs(cp - tech["ema21"]) / cp < 0.025 or abs(cp - tech["ma50"]) / cp < 0.025
        rsi_range  = 33 <= rsi <= 58
        vol_ok     = tech["vol_trend"] in ("falling", "flat")  # healthy pullback = declining vol

        if rsi_range and (at_support or at_ema) and vol_ok:
            return SETUP_PULLBACK

    # ── BREAKOUT ──────────────────────────────────────────────────────────────
    # Price near resistance, strong volume, bullish MACD
    if resistances:
        near_resistance = abs(cp - resistances[0]) / cp < 0.025
        macd_bullish    = tech["macd"] > tech["macd_signal"]
        vol_strong      = vol_pct >= 15
        rsi_ok          = 45 <= rsi <= 72

        if near_resistance and macd_bullish and vol_strong and rsi_ok:
            return SETUP_BREAKOUT

    # ── REVERSAL ─────────────────────────────────────────────────────────────
    # Oversold, at support, divergence hint
    oversold       = rsi < 35
    weekly_oversold = tech.get("weekly_rsi", 50) < 40
    at_support     = supports and abs(cp - supports[0]) / cp < 0.04

    if oversold and (at_support or weekly_oversold):
        return SETUP_REVERSAL

    return SETUP_WAIT


def generate_entry_plan(tech: dict, setup: str) -> dict:
    """
    Generate entry zone, style, confirmation needed, and invalidation condition.
    """
    cp          = tech["current_price"]
    resistances = tech["resistances"]
    supports    = tech["supports"]
    atr         = tech["atr14"]

    if setup == SETUP_BREAKOUT:
        res = resistances[0] if resistances else cp * 1.02
        return {
            "style":        "Breakout",
            "entry_low":    round(res * 0.998, 2),
            "entry_high":   round(res * 1.015, 2),
            "confirmation": f"Daily close above ${res:.2f} with volume > 120% of avg",
            "invalidation": f"Close back below ${round(res * 0.985, 2)} (failed breakout)",
        }

    elif setup == SETUP_PULLBACK:
        sup = supports[0] if supports else tech["ema21"]
        return {
            "style":        "Pullback to support",
            "entry_low":    round(sup * 0.995, 2),
            "entry_high":   round(sup * 1.012, 2),
            "confirmation": f"Bullish candle or RSI turning up from near ${sup:.2f}",
            "invalidation": f"Daily close below ${round(sup * 0.988, 2)} (support lost)",
        }

    elif setup == SETUP_REVERSAL:
        return {
            "style":        "Reversal at support",
            "entry_low":    round(cp * 0.99, 2),
            "entry_high":   round(cp * 1.02, 2),
            "confirmation": f"Volume spike + close above prior day high ${round(cp * 1.01, 2)}",
            "invalidation": f"New closing low below ${round(tech['recent_swing_low'] * 0.99, 2)}",
        }

    else:  # WAIT
        return {
            "style":        "No clear entry — watch",
            "entry_low":    None,
            "entry_high":   None,
            "confirmation": "No setup triggered. Monitor for pullback or breakout conditions.",
            "invalidation": "N/A",
        }


def generate_exit_plan(tech: dict, entry: dict, setup: str, risk_config: dict) -> dict:
    """
    Generate stop-loss, T1, T2, trailing stop, time exit, early exit conditions.
    """
    cp          = tech["current_price"]
    atr         = tech["atr14"]
    resistances = tech["resistances"]
    supports    = tech["supports"]
    entry_price = entry.get("entry_low") or cp  # use lower bound of entry zone

    # ── Stop-Loss ──────────────────────────────────────────────────────────────
    if setup == SETUP_BREAKOUT:
        res = (tech["resistances"][0] if tech["resistances"] else cp)
        stop = round(min(res * 0.985, entry_price - 1.5 * atr), 2)
    elif setup == SETUP_PULLBACK:
        sup = (supports[0] if supports else tech["ema21"])
        stop = round(min(sup * 0.988, entry_price - 1.2 * atr), 2)
    elif setup == SETUP_REVERSAL:
        stop = round(min(tech["recent_swing_low"] * 0.985, entry_price - 1.5 * atr), 2)
    else:
        stop = round(entry_price - 2 * atr, 2)

    stop = max(stop, round(entry_price * 0.85, 2))  # hard floor: no > 15% stop

    stop_distance = round(entry_price - stop, 2)

    # ── Targets ───────────────────────────────────────────────────────────────
    # T1: nearest resistance above entry, minimum 1.5x stop distance
    min_t1 = round(entry_price + 1.5 * stop_distance, 2)
    if resistances and resistances[0] > entry_price:
        t1 = max(resistances[0], min_t1)
    else:
        t1 = round(entry_price + 1.5 * atr, 2)
    t1 = round(t1, 2)

    # T2: second resistance or 3x ATR
    min_t2 = round(entry_price + 3.0 * stop_distance, 2)
    if len(resistances) >= 2 and resistances[1] > t1:
        t2 = max(resistances[1], min_t2)
    elif tech["w_resistances"] and tech["w_resistances"][0] > t1:
        t2 = max(tech["w_resistances"][0], min_t2)
    else:
        t2 = round(entry_price + 3.0 * atr, 2)
    t2 = round(t2, 2)

    # ── Risk / Reward ──────────────────────────────────────────────────────────
    rr1 = round((t1 - entry_price) / stop_distance, 1) if stop_distance > 0 else 0
    rr2 = round((t2 - entry_price) / stop_distance, 1) if stop_distance > 0 else 0

    # ── Time-based exit ────────────────────────────────────────────────────────
    time_exit_days = {
        SETUP_BREAKOUT:  risk_config.get("breakout_time_exit_days",  5),
        SETUP_PULLBACK:  risk_config.get("pullback_time_exit_days",  3),
        SETUP_REVERSAL:  risk_config.get("reversal_time_exit_days",  7),
        SETUP_WAIT:      3,
    }[setup]

    trailing_trigger = round(entry_price + (t1 - entry_price) * 0.5, 2)

    return {
        "stop":              stop,
        "stop_distance":     stop_distance,
        "t1":                t1,
        "t2":                t2,
        "rr1":               rr1,
        "rr2":               rr2,
        "trailing_trigger":  trailing_trigger,
        "trailing_rule":     f"Move stop to entry (breakeven) once price hits ${trailing_trigger:.2f}",
        "time_exit_days":    time_exit_days,
        "early_exit": [
            "Volume dries up on rally (vol < 60% of avg while moving toward target)",
            "RSI hits 80+ before T1 — take partial profits",
            "MACD histogram reverses before target reached",
            f"Broad market (SPY) drops > 1.5% intraday",
        ],
    }
