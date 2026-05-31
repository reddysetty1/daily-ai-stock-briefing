"""
tech_analysis.py — Shared technical analysis module.
Used by daily_scan.py and importable by other modules.
predict.py keeps its own copy of these functions to avoid breaking the bot.
"""

import logging
import numpy as np
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


# ── Indicator Calculations ────────────────────────────────────────────────────

def calculate_rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(window=period).mean()
    loss  = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else 50.0


def calculate_macd(closes: pd.Series):
    """Returns (macd_line, signal_line, histogram)."""
    ema12  = closes.ewm(span=12, adjust=False).mean()
    ema26  = closes.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    return (
        round(float(macd.iloc[-1]),   4),
        round(float(signal.iloc[-1]), 4),
        round(float(hist.iloc[-1]),   4),
    )


def calculate_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> float:
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return round(float(atr), 2) if pd.notna(atr) else 0.0


def calculate_bollinger(closes: pd.Series, period: int = 20):
    """Returns (upper, middle, lower) Bollinger Bands."""
    mid = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    return (
        round(float((mid + 2 * std).iloc[-1]), 2),
        round(float(mid.iloc[-1]),              2),
        round(float((mid - 2 * std).iloc[-1]), 2),
    )


def find_support_resistance(highs: pd.Series, lows: pd.Series, window: int = 10):
    """Find top 3 support and resistance levels from recent swing points."""
    resistance_levels, support_levels = [], []

    for i in range(window, len(highs) - window):
        if highs.iloc[i] == highs.iloc[i - window:i + window].max():
            resistance_levels.append(round(float(highs.iloc[i]), 2))
        if lows.iloc[i] == lows.iloc[i - window:i + window].min():
            support_levels.append(round(float(lows.iloc[i]), 2))

    current = float(highs.iloc[-1])

    def dedupe(levels):
        levels = sorted(set(levels))
        deduped = []
        for lvl in levels:
            if not deduped or abs(lvl - deduped[-1]) / max(current, 1) > 0.005:
                deduped.append(lvl)
        return deduped

    resistance_levels = dedupe(resistance_levels)
    support_levels    = dedupe(support_levels)

    resistances = sorted([r for r in resistance_levels if r > current])[:3]
    supports    = sorted([s for s in support_levels    if s < current], reverse=True)[:3]
    return supports, resistances


def classify_price_attractiveness(current: float, ma50: float, ma200: float,
                                   week52_high: float, week52_low: float, rsi: float) -> str:
    """Classify whether the current price is a good entry from an attractiveness standpoint."""
    if week52_high <= 0:
        return "unknown"
    pct_from_52wk_high = (week52_high - current) / week52_high
    pct_above_200ma    = (current - ma200) / ma200 if ma200 > 0 else 0

    if rsi > 75 or pct_from_52wk_high < 0.02:
        return "overbought"
    elif rsi > 65 or pct_above_200ma > 0.25:
        return "extended"
    elif rsi < 35 or (current < ma200 and current < ma50):
        return "oversold / watch-for-reversal"
    elif current > ma50 and pct_from_52wk_high > 0.08:
        return "attractive"
    else:
        return "fair"


# ── Full Technical Data Fetch ─────────────────────────────────────────────────

def fetch_full_technical(ticker: str) -> dict:
    """
    Fetch and compute all technical indicators for a ticker.
    Returns a structured dict. Raises ValueError for invalid tickers.
    """
    t = yf.Ticker(ticker)

    info = t.fast_info
    try:
        current_price = float(info.last_price)
        if not current_price or current_price <= 0:
            raise ValueError("Invalid price")
    except Exception:
        raise ValueError(f"'{ticker}' not found or market closed.")

    hist = t.history(period="1y")
    if hist.empty or len(hist) < 50:
        raise ValueError(f"Not enough history for '{ticker}'.")

    closes  = hist["Close"]
    highs   = hist["High"]
    lows    = hist["Low"]
    volumes = hist["Volume"]

    # ── Daily MAs ──────────────────────────────────────────────────────────────
    ma20  = round(float(closes.rolling(20).mean().iloc[-1]),  2)
    ma50  = round(float(closes.rolling(50).mean().iloc[-1]),  2)
    ma200 = round(float(closes.rolling(200).mean().iloc[-1]), 2)
    ema9  = round(float(closes.ewm(span=9,  adjust=False).mean().iloc[-1]), 2)
    ema21 = round(float(closes.ewm(span=21, adjust=False).mean().iloc[-1]), 2)

    # ── Trend ──────────────────────────────────────────────────────────────────
    above_50  = current_price > ma50
    above_200 = current_price > ma200
    golden_cross = ma50 > ma200
    if above_50 and above_200 and golden_cross:
        trend = "Strong Bullish"
    elif above_50 and above_200:
        trend = "Bullish"
    elif above_50 and not above_200:
        trend = "Cautiously Bullish"
    elif not above_50 and above_200:
        trend = "Cautiously Bearish"
    else:
        trend = "Bearish"

    # ── Indicators ─────────────────────────────────────────────────────────────
    rsi             = calculate_rsi(closes)
    macd_val, macd_signal, macd_hist = calculate_macd(closes)
    atr14           = calculate_atr(highs, lows, closes)
    bb_upper, bb_mid, bb_lower = calculate_bollinger(closes)
    supports, resistances = find_support_resistance(highs.tail(60), lows.tail(60))

    # ── Volume ─────────────────────────────────────────────────────────────────
    avg_vol_20  = round(float(volumes.rolling(20).mean().iloc[-1]))
    avg_vol_5   = round(float(volumes.tail(5).mean()))
    vol_trend   = "rising" if avg_vol_5 > avg_vol_20 * 1.1 else \
                  "falling" if avg_vol_5 < avg_vol_20 * 0.9 else "flat"
    today_vol   = int(info.three_month_average_volume or avg_vol_20)
    vol_pct     = round((today_vol / avg_vol_20 - 1) * 100, 1) if avg_vol_20 else 0

    # ── Weekly ─────────────────────────────────────────────────────────────────
    weekly = t.history(period="6mo", interval="1wk")
    w_closes = weekly["Close"] if not weekly.empty else closes.tail(26)
    w_highs  = weekly["High"]  if not weekly.empty else highs.tail(26)
    w_lows   = weekly["Low"]   if not weekly.empty else lows.tail(26)

    wma10        = round(float(w_closes.rolling(10).mean().iloc[-1]), 2) if len(w_closes) >= 10 else ma50
    weekly_rsi   = calculate_rsi(w_closes, period=14) if len(w_closes) >= 20 else rsi
    w_macd, w_sig, w_hist = calculate_macd(w_closes) if len(w_closes) >= 26 else (macd_val, macd_signal, macd_hist)
    w_supports, w_resistances = find_support_resistance(w_highs, w_lows, window=3) \
                                  if len(w_highs) > 6 else (supports, resistances)

    week_high_so_far = round(float(w_highs.iloc[-1]), 2) if not weekly.empty else current_price
    week_low_so_far  = round(float(w_lows.iloc[-1]),  2) if not weekly.empty else current_price

    # ── 52-week ────────────────────────────────────────────────────────────────
    week52_high = round(float(info.year_high), 2)
    week52_low  = round(float(info.year_low),  2)

    # ── Price attractiveness ───────────────────────────────────────────────────
    attractiveness = classify_price_attractiveness(
        current_price, ma50, ma200, week52_high, week52_low, rsi
    )

    # ── Previous day stats ──────────────────────────────────────────────────────
    prev_close     = round(float(info.previous_close), 2)
    day_change     = round(current_price - prev_close, 2)
    day_change_pct = round(day_change / prev_close * 100, 2) if prev_close else 0

    # ── Recent swing low (for reversal stop) ───────────────────────────────────
    recent_swing_low = round(float(lows.tail(5).min()), 2)

    return {
        # Price
        "ticker":            ticker,
        "current_price":     round(current_price, 2),
        "prev_close":        prev_close,
        "day_change":        day_change,
        "day_change_pct":    day_change_pct,
        "week52_high":       week52_high,
        "week52_low":        week52_low,
        "week_high_so_far":  week_high_so_far,
        "week_low_so_far":   week_low_so_far,
        "recent_swing_low":  recent_swing_low,
        # Daily MAs
        "ma20":              ma20,
        "ma50":              ma50,
        "ma200":             ma200,
        "ema9":              ema9,
        "ema21":             ema21,
        "trend":             trend,
        "golden_cross":      golden_cross,
        # Indicators
        "rsi":               rsi,
        "macd":              macd_val,
        "macd_signal":       macd_signal,
        "macd_hist":         macd_hist,
        "atr14":             atr14,
        "bb_upper":          bb_upper,
        "bb_mid":            bb_mid,
        "bb_lower":          bb_lower,
        # Volume
        "today_vol":         today_vol,
        "avg_vol_20":        avg_vol_20,
        "vol_pct":           vol_pct,
        "vol_trend":         vol_trend,
        # S/R
        "supports":          supports,
        "resistances":       resistances,
        # Weekly
        "wma10":             wma10,
        "weekly_rsi":        weekly_rsi,
        "w_macd":            w_macd,
        "w_signal":          w_sig,
        "w_hist":            w_hist,
        "w_supports":        w_supports,
        "w_resistances":     w_resistances,
        # Classification
        "attractiveness":    attractiveness,
    }
