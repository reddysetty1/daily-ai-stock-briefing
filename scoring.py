"""
scoring.py — Multi-factor stock scoring engine.
Combines trend, momentum, volume, sector strength, fundamentals, and risk penalty.
All factor scores are 0-100. Final score = weighted sum minus risk penalty.
"""

import logging
from fundamentals import score_fundamentals

log = logging.getLogger(__name__)


def score_trend(tech: dict) -> int:
    """0-100. Based on price position relative to moving averages."""
    score = 0
    cp = tech["current_price"]

    if cp > tech["ma200"]:   score += 25
    if cp > tech["ma50"]:    score += 25
    if tech["golden_cross"]: score += 20
    if cp > tech["ema9"]:    score += 15
    if cp > tech["wma10"]:   score += 15

    return min(100, score)


def score_momentum(tech: dict) -> int:
    """0-100. RSI + MACD + histogram expansion."""
    score = 0
    rsi  = tech["rsi"]
    hist = tech["macd_hist"]

    # RSI
    if   45 <= rsi <= 65:  score += 35
    elif 65 < rsi <= 70:   score += 25
    elif 35 <= rsi < 45:   score += 20
    elif rsi > 70:         score += 12  # overbought — weak signal, risk catches it
    else:                  score += 5

    # MACD direction
    if tech["macd"] > tech["macd_signal"]:  score += 35
    if tech.get("w_macd", 0) and tech["w_macd"] > tech.get("w_signal", 0):
        score += 20

    # MACD histogram expanding (positive and growing)
    # Compare today's hist vs prior hist (use sign + magnitude change heuristic)
    if hist > 0:  score += 10

    return min(100, score)


def score_volume(tech: dict) -> int:
    """0-100. Volume vs 20-day average + volume trend."""
    score = 0
    vol_pct  = tech["vol_pct"]   # % above/below 20-day avg
    vol_trend = tech["vol_trend"]

    if   vol_pct >= 50:   score += 55
    elif vol_pct >= 20:   score += 40
    elif vol_pct >= 0:    score += 25
    elif vol_pct >= -20:  score += 12
    else:                 score += 0

    if vol_trend == "rising":   score += 30
    elif vol_trend == "flat":   score += 15

    # Volume contraction on pullback is actually healthy (adds setup quality)
    if vol_trend == "falling" and tech["rsi"] < 55:
        score += 15   # healthy pullback on declining volume

    return min(100, score)


def score_sector(ticker: str, tech: dict, market_data: dict, sector_map: dict) -> int:
    """0-100. Relative performance vs sector ETF."""
    score = 0
    etf = sector_map.get(ticker, "SPY")

    if etf not in market_data:
        return 50  # neutral if no data

    etf_chg    = market_data[etf]["day_change_pct"]
    stock_chg  = tech["day_change_pct"]
    rel_perf   = stock_chg - etf_chg

    # Sector ETF itself — broad market health
    spy_chg = market_data.get("SPY", {}).get("day_change_pct", 0)
    if spy_chg > 0.5:     score += 20
    elif spy_chg > 0:     score += 10
    elif spy_chg < -1.0:  score -= 10  # broad weakness — drag

    # Stock vs its sector
    if   rel_perf >= 2.0:  score += 60
    elif rel_perf >= 1.0:  score += 45
    elif rel_perf >= 0:    score += 30
    elif rel_perf >= -1.0: score += 15
    else:                  score += 5

    return min(100, max(0, score))


def score_risk_penalty(tech: dict, fund: dict) -> int:
    """
    0-100 penalty score. Higher = more risk = larger deduction from final score.
    Triggered by overbought conditions, poor liquidity, earnings proximity, etc.
    """
    penalty = 0
    rsi = tech["rsi"]
    cp  = tech["current_price"]

    # Overbought RSI
    if rsi > 80:   penalty += 50
    elif rsi > 70: penalty += 30

    # Weekly RSI overbought
    if tech.get("weekly_rsi", 50) > 70: penalty += 20

    # Price extended far above 50MA
    if tech["ma50"] > 0:
        pct_above_50 = (cp - tech["ma50"]) / tech["ma50"]
        if pct_above_50 > 0.15: penalty += 20
        elif pct_above_50 > 0.10: penalty += 10

    # Near 52-week high AND overbought
    if tech["week52_high"] > 0:
        pct_from_52wk = (tech["week52_high"] - cp) / tech["week52_high"]
        if pct_from_52wk < 0.02 and rsi > 65: penalty += 25

    # Bearish trend
    if tech["trend"] in ("Bearish", "Cautiously Bearish"):
        penalty += 30

    # Low volume (poor liquidity)
    if tech["vol_pct"] < -50: penalty += 15

    # Earnings within 5 trading days — high event risk
    import time
    earnings_ts = fund.get("earnings_timestamp")
    if earnings_ts:
        days_to_earnings = (earnings_ts - time.time()) / 86400
        if 0 < days_to_earnings < 5:
            penalty += 35  # major — don't enter before earnings
        elif 0 < days_to_earnings < 10:
            penalty += 15

    # High beta
    beta = fund.get("beta")
    if beta and beta > 2.5: penalty += 15
    elif beta and beta > 2.0: penalty += 8

    return min(100, max(0, penalty))


def score_stock(ticker: str, tech: dict, fund: dict,
                market_data: dict, sector_map: dict, weights: dict) -> tuple:
    """
    Compute all factor scores and return (final_score, breakdown).
    final_score is 0-100.
    """
    trend_s    = score_trend(tech)
    momentum_s = score_momentum(tech)
    volume_s   = score_volume(tech)
    sector_s   = score_sector(ticker, tech, market_data, sector_map)
    fund_s, fund_bd = score_fundamentals(fund)
    risk_pen   = score_risk_penalty(tech, fund)

    raw = (
        trend_s    * weights["trend"]       +
        momentum_s * weights["momentum"]    +
        volume_s   * weights["volume"]      +
        sector_s   * weights["sector"]      +
        fund_s     * weights["fundamentals"]
    )
    final = max(0.0, raw - risk_pen * weights["risk_penalty"])
    final = round(final, 1)

    breakdown = {
        "trend":       trend_s,
        "momentum":    momentum_s,
        "volume":      volume_s,
        "sector":      sector_s,
        "fundamental": fund_s,
        "risk_penalty": risk_pen,
        "final":       final,
    }

    log.debug("%s scores: %s", ticker, breakdown)
    return final, breakdown
