"""
risk.py — Position sizing and risk management.
Stop-distance-first approach: stop is determined by setup, size follows from risk %.
"""

import logging

log = logging.getLogger(__name__)


def calculate_position_size(entry_price: float, stop_price: float,
                             account_size: float, risk_pct: float,
                             max_position_pct: float) -> dict:
    """
    Calculate position size from account risk parameters.

    Args:
        entry_price:      Planned entry price (use entry_low from entry plan)
        stop_price:       Stop-loss price
        account_size:     Total account value in $
        risk_pct:         Max % of account to risk on this trade (e.g. 0.01 = 1%)
        max_position_pct: Max % of account in a single position (e.g. 0.15 = 15%)

    Returns dict with shares, position_value, risk_dollars, actual_risk_pct, stop_distance.
    """
    if entry_price <= 0 or stop_price >= entry_price:
        return _zero_position(entry_price)

    stop_distance = round(entry_price - stop_price, 2)
    risk_dollars  = round(account_size * risk_pct, 2)
    raw_shares    = risk_dollars / stop_distance

    # Apply position value cap
    max_value     = account_size * max_position_pct
    cap_shares    = max_value / entry_price

    shares        = max(1, int(min(raw_shares, cap_shares)))
    position_val  = round(shares * entry_price, 2)
    actual_risk   = round(shares * stop_distance, 2)
    actual_risk_pct = round(actual_risk / account_size * 100, 2)

    return {
        "shares":           shares,
        "position_value":   position_val,
        "position_pct":     round(position_val / account_size * 100, 1),
        "risk_dollars":     actual_risk,
        "risk_pct":         actual_risk_pct,
        "stop_distance":    stop_distance,
    }


def _zero_position(entry_price: float) -> dict:
    return {
        "shares": 0, "position_value": 0.0, "position_pct": 0.0,
        "risk_dollars": 0.0, "risk_pct": 0.0, "stop_distance": 0.0,
    }


def check_portfolio_guards(new_ticker_sector: str, existing_positions: list,
                            max_per_sector: int = 2) -> tuple:
    """
    Check if adding this position violates portfolio-level rules.
    Returns (allowed: bool, reason: str).
    """
    sector_count = sum(1 for p in existing_positions if p.get("sector") == new_ticker_sector)
    if sector_count >= max_per_sector:
        return False, f"Already {sector_count} positions in {new_ticker_sector} (max {max_per_sector})"
    return True, "OK"


def assess_setup_quality(rr1: float, rr2: float, min_rr: float,
                          setup: str, tech: dict) -> tuple:
    """
    Returns (quality: str, flags: list of warning strings).
    Quality: 'good', 'marginal', 'poor'
    """
    flags = []

    if rr1 < min_rr:
        flags.append(f"R:R {rr1:.1f}:1 below minimum {min_rr:.1f}:1 — marginal setup")

    if tech["vol_pct"] < -30:
        flags.append("Volume well below average — low conviction")

    if tech["rsi"] > 72:
        flags.append("RSI overbought — avoid chasing; wait for pullback")

    if tech["attractiveness"] in ("overbought", "extended"):
        flags.append(f"Price is {tech['attractiveness']} — higher entry risk")

    if setup == "wait":
        flags.append("No clean setup detected — watchlist only")

    if rr1 >= min_rr and not flags:
        quality = "good"
    elif rr1 >= min_rr * 0.8:
        quality = "marginal"
    else:
        quality = "poor"

    return quality, flags
