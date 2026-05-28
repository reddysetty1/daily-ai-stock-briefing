"""
predict.py — Intraday High/Low Predictor
Given a stock ticker, calculates technical indicators and uses Gemini
to estimate the day's high and low. Sends result to Telegram.

Usage:
    STOCK_TICKER=AAPL python predict.py
"""

import os
import sys
import logging
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import requests
from google import genai
from google.genai import types
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_MODEL       = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
STOCK_TICKER       = os.getenv("STOCK_TICKER", "").upper().strip()

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

# ── Logging ───────────────────────────────────────────────────────────────────

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Technical Analysis ────────────────────────────────────────────────────────

def calculate_rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(window=period).mean()
    loss  = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)

def calculate_macd(closes: pd.Series):
    ema12  = closes.ewm(span=12, adjust=False).mean()
    ema26  = closes.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    return round(float(macd.iloc[-1]), 4), round(float(signal.iloc[-1]), 4), round(float(hist.iloc[-1]), 4)

def find_support_resistance(highs: pd.Series, lows: pd.Series, window: int = 10) -> tuple:
    """Find top 3 support and resistance levels from recent swing points."""
    resistance_levels = []
    support_levels    = []

    for i in range(window, len(highs) - window):
        if highs.iloc[i] == highs.iloc[i-window:i+window].max():
            resistance_levels.append(round(float(highs.iloc[i]), 2))
        if lows.iloc[i] == lows.iloc[i-window:i+window].min():
            support_levels.append(round(float(lows.iloc[i]), 2))

    # Deduplicate close levels (within 0.5% of each other)
    def dedupe(levels, current_price):
        levels = sorted(set(levels))
        deduped = []
        for lvl in levels:
            if not deduped or abs(lvl - deduped[-1]) / current_price > 0.005:
                deduped.append(lvl)
        return deduped

    current = float(highs.iloc[-1])
    resistance_levels = dedupe(resistance_levels, current)
    support_levels    = dedupe(support_levels, current)

    # Return levels closest to current price
    resistances = sorted([r for r in resistance_levels if r > current])[:3]
    supports    = sorted([s for s in support_levels    if s < current], reverse=True)[:3]

    return supports, resistances

def calculate_vwap(df_intraday: pd.DataFrame) -> float:
    typical_price = (df_intraday["High"] + df_intraday["Low"] + df_intraday["Close"]) / 3
    vwap = (typical_price * df_intraday["Volume"]).cumsum() / df_intraday["Volume"].cumsum()
    return round(float(vwap.iloc[-1]), 2)

def fetch_technical_data(ticker: str) -> dict:
    log.info("Fetching data for %s...", ticker)
    t = yf.Ticker(ticker)

    # Validate ticker
    info = t.fast_info
    try:
        current_price = float(info.last_price)
        if not current_price or current_price <= 0:
            raise ValueError("Invalid price")
    except Exception:
        raise ValueError(f"Ticker '{ticker}' not found or market is closed.")

    # 1 year daily history for indicators
    hist = t.history(period="1y")
    if hist.empty or len(hist) < 50:
        raise ValueError(f"Not enough historical data for '{ticker}'.")

    closes  = hist["Close"]
    highs   = hist["High"]
    lows    = hist["Low"]
    volumes = hist["Volume"]

    # Moving averages
    ma50  = round(float(closes.rolling(50).mean().iloc[-1]), 2)
    ma200 = round(float(closes.rolling(200).mean().iloc[-1]), 2)
    ema9  = round(float(closes.ewm(span=9,  adjust=False).mean().iloc[-1]), 2)
    ema21 = round(float(closes.ewm(span=21, adjust=False).mean().iloc[-1]), 2)

    # Trend
    above_50  = current_price > ma50
    above_200 = current_price > ma200
    if above_50 and above_200:
        trend = "Bullish (above 50MA and 200MA)"
    elif above_50 and not above_200:
        trend = "Cautiously Bullish (above 50MA, below 200MA)"
    elif not above_50 and above_200:
        trend = "Cautiously Bearish (below 50MA, above 200MA)"
    else:
        trend = "Bearish (below 50MA and 200MA)"

    # RSI
    rsi = calculate_rsi(closes)
    if rsi >= 70:
        rsi_label = "Overbought"
    elif rsi <= 30:
        rsi_label = "Oversold"
    else:
        rsi_label = "Neutral"

    # MACD
    macd_val, macd_signal, macd_hist = calculate_macd(closes)
    if macd_val > macd_signal:
        macd_label = "Bullish (MACD above signal)"
    else:
        macd_label = "Bearish (MACD below signal)"

    # Volume
    avg_vol_20    = round(float(volumes.rolling(20).mean().iloc[-1]))
    today_vol     = int(info.three_month_average_volume or avg_vol_20)
    vol_pct       = round((today_vol / avg_vol_20 - 1) * 100, 1) if avg_vol_20 else 0
    vol_label     = f"{abs(vol_pct)}% {'above' if vol_pct >= 0 else 'below'} 20-day avg"

    # Support / Resistance (last 60 days daily)
    supports, resistances = find_support_resistance(highs.tail(60), lows.tail(60))

    # Weekly data — last 26 weeks for weekly-level technicals
    weekly = t.history(period="6mo", interval="1wk")
    w_closes  = weekly["Close"]
    w_highs   = weekly["High"]
    w_lows    = weekly["Low"]

    week_open      = round(float(weekly["Open"].iloc[-1]),  2) if not weekly.empty else current_price
    week_high_so_far = round(float(w_highs.iloc[-1]),  2) if not weekly.empty else current_price
    week_low_so_far  = round(float(w_lows.iloc[-1]),   2) if not weekly.empty else current_price

    # Weekly moving averages (10-week ≈ 50-day, 40-week ≈ 200-day)
    wma10 = round(float(w_closes.rolling(10).mean().iloc[-1]), 2) if len(w_closes) >= 10 else None
    wma20 = round(float(w_closes.rolling(20).mean().iloc[-1]), 2) if len(w_closes) >= 20 else None

    # Weekly RSI & MACD
    weekly_rsi = calculate_rsi(w_closes, period=14) if len(w_closes) >= 20 else None
    if weekly_rsi:
        if weekly_rsi >= 70:
            weekly_rsi_label = "Overbought"
        elif weekly_rsi <= 30:
            weekly_rsi_label = "Oversold"
        else:
            weekly_rsi_label = "Neutral"
    else:
        weekly_rsi_label = "N/A"

    w_macd, w_signal, w_hist = calculate_macd(w_closes) if len(w_closes) >= 26 else (None, None, None)
    weekly_macd_label = "Bullish" if (w_macd and w_macd > w_signal) else "Bearish"

    # Weekly support / resistance (last 26 weeks)
    w_supports, w_resistances = find_support_resistance(w_highs, w_lows, window=3)

    # Today's intraday data
    intraday = t.history(period="1d", interval="5m")
    today_open  = round(float(intraday["Open"].iloc[0]),  2) if not intraday.empty else current_price
    today_high  = round(float(intraday["High"].max()),    2) if not intraday.empty else current_price
    today_low   = round(float(intraday["Low"].min()),     2) if not intraday.empty else current_price
    vwap        = calculate_vwap(intraday)                   if not intraday.empty else current_price

    # Previous day stats
    prev_close  = round(float(info.previous_close), 2)
    day_change  = round(current_price - prev_close,  2)
    day_change_pct = round(day_change / prev_close * 100, 2)

    # 52-week high/low
    week52_high = round(float(info.year_high), 2)
    week52_low  = round(float(info.year_low),  2)

    return {
        "ticker":         ticker,
        "current_price":  round(current_price, 2),
        "prev_close":     prev_close,
        "day_change":     day_change,
        "day_change_pct": day_change_pct,
        "today_open":     today_open,
        "today_high":     today_high,
        "today_low":      today_low,
        "vwap":           vwap,
        "week52_high":    week52_high,
        "week52_low":     week52_low,
        "ma50":           ma50,
        "ma200":          ma200,
        "ema9":           ema9,
        "ema21":          ema21,
        "trend":          trend,
        "rsi":            rsi,
        "rsi_label":      rsi_label,
        "macd":           macd_val,
        "macd_signal":    macd_signal,
        "macd_hist":      macd_hist,
        "macd_label":     macd_label,
        "volume":         today_vol,
        "avg_vol_20":     avg_vol_20,
        "vol_label":      vol_label,
        "supports":           supports,
        "resistances":        resistances,
        "week_open":          week_open,
        "week_high_so_far":   week_high_so_far,
        "week_low_so_far":    week_low_so_far,
        "wma10":              wma10,
        "wma20":              wma20,
        "weekly_rsi":         weekly_rsi,
        "weekly_rsi_label":   weekly_rsi_label,
        "weekly_macd_label":  weekly_macd_label,
        "w_supports":         w_supports,
        "w_resistances":      w_resistances,
    }

def format_tech_data(d: dict) -> str:
    sup   = " / ".join([f"${s}" for s in d["supports"]])      or "N/A"
    res   = " / ".join([f"${r}" for r in d["resistances"]])   or "N/A"
    w_sup = " / ".join([f"${s}" for s in d["w_supports"]])    or "N/A"
    w_res = " / ".join([f"${r}" for r in d["w_resistances"]]) or "N/A"
    arrow = "up" if d["day_change"] >= 0 else "down"

    return f"""
Ticker: {d['ticker']}
Current Price: ${d['current_price']} ({arrow} ${abs(d['day_change'])} / {d['day_change_pct']}% today)
Previous Close: ${d['prev_close']}

TODAY SO FAR:
  Open: ${d['today_open']}
  High: ${d['today_high']}
  Low:  ${d['today_low']}
  VWAP: ${d['vwap']}

THIS WEEK SO FAR:
  Week Open:         ${d['week_open']}
  Week High so far:  ${d['week_high_so_far']}
  Week Low so far:   ${d['week_low_so_far']}

52-WEEK RANGE: ${d['week52_low']} — ${d['week52_high']}

DAILY MOVING AVERAGES:
  50-day MA:  ${d['ma50']}  (price is {'above' if d['current_price'] > d['ma50'] else 'below'})
  200-day MA: ${d['ma200']} (price is {'above' if d['current_price'] > d['ma200'] else 'below'})
  9-day EMA:  ${d['ema9']}
  21-day EMA: ${d['ema21']}
  Trend: {d['trend']}

WEEKLY MOVING AVERAGES:
  10-week MA: ${d['wma10'] or 'N/A'}
  20-week MA: ${d['wma20'] or 'N/A'}

DAILY RSI (14): {d['rsi']} — {d['rsi_label']}
WEEKLY RSI (14): {d['weekly_rsi'] or 'N/A'} — {d['weekly_rsi_label']}

DAILY MACD:
  MACD Line:   {d['macd']}
  Signal Line: {d['macd_signal']}
  Histogram:   {d['macd_hist']}
  Status: {d['macd_label']}
WEEKLY MACD: {d['weekly_macd_label']}

VOLUME:
  Today: {d['volume']:,}
  20-day avg: {d['avg_vol_20']:,}
  Status: {d['vol_label']}

DAILY SUPPORT:     {sup}
DAILY RESISTANCE:  {res}
WEEKLY SUPPORT:    {w_sup}
WEEKLY RESISTANCE: {w_res}
""".strip()

# ── Gemini Prediction ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a professional technical analyst. You are given full daily and weekly technical data for a stock.
Your job is to estimate:
  1. Today's likely HIGH and LOW for the remainder of the session
  2. This week's expected HIGH and LOW — with recommended entry and exit prices for a short-term trade
Base ALL estimates ONLY on the technical data provided. Be specific with dollar amounts.
Do NOT use markdown, asterisks, or bold. Plain text only. Use the emoji prefix shown exactly.

Output EXACTLY this format — no extra lines, no deviations:

🎯 [TICKER] — Price Prediction

📅 [Day, Month Date Year]

— CURRENT STATUS —
Price: $[price] | Open: $[open]
Today High: $[high] | Today Low: $[low]
VWAP: $[vwap]

— TECHNICAL SNAPSHOT —
Daily Trend: [trend]
Daily RSI(14): [value] — [label + brief implication]
Weekly RSI(14): [value] — [label + brief implication]
MACD (Daily): [bullish/bearish + brief note]
MACD (Weekly): [bullish/bearish + brief note]
Volume: [vol label — what it signals]
Daily Support: [levels] | Daily Resistance: [levels]
Weekly Support: [levels] | Weekly Resistance: [levels]

— TODAY'S ESTIMATE —
📈 Today High: $[price]
📉 Today Low:  $[price]
📊 Confidence: [Low / Moderate / High] — [one-line reason]

— THIS WEEK'S ESTIMATE (Short-Term Trade) —
🗺️ Expected Path: [Describe the sequence clearly, e.g. "Rally to $X first by [date], then pull back to $Y by [date]" OR "Drop to $X first by [date], then recover to $Y by [date]"]
📉 Week Low:  $[price] — expected by [Day, Month Date]
📈 Week High: $[price] — expected by [Day, Month Date]
🟢 Entry:  $[price] around [Day, Month Date] — [one-line reason]
🔴 Exit:   $[price] around [Day, Month Date] — [one-line reason]
🔄 Recovery after exit? [Yes — likely to recover to $X by [date] / No — expect further downside after exit]
📊 Confidence: [Low / Moderate / High] — [one-line reason]

— REASONING —
[3-4 tight sentences: explain the expected price path sequence — which move comes first and why, what triggers the reversal, and whether recovery is expected after the exit]

⚠️ Note: Technical analysis only. Not financial advice."""

def fetch_prediction(tech_data_str: str) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)

    log.info("Calling Gemini for prediction (model=%s)...", GEMINI_MODEL)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            f"Today is {datetime.now().strftime('%A, %B %d, %Y')}.\n\n"
            f"Technical data:\n{tech_data_str}\n\n"
            "Generate the intraday high/low prediction. Strict format only."
        ),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=800,
            temperature=0.1,
        ),
    )

    text = response.text.strip()
    log.info("Gemini OK - %d chars received.", len(text))
    return text

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    url  = TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN)
    body = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}

    resp = requests.post(url, json=body, timeout=30)
    if resp.status_code == 200 and resp.json().get("ok"):
        log.info("Telegram message sent successfully.")
        return True
    log.error("Telegram error %s: %s", resp.status_code, resp.text)
    return False

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not STOCK_TICKER:
        log.error("STOCK_TICKER env var not set. Example: STOCK_TICKER=AAPL python predict.py")
        sys.exit(1)
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not set.")
        sys.exit(1)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
        sys.exit(1)

    log.info("=== Intraday Predictor: %s ===", STOCK_TICKER)
    try:
        data        = fetch_technical_data(STOCK_TICKER)
        data_str    = format_tech_data(data)
        log.info("Technical data:\n%s", data_str)

        prediction  = fetch_prediction(data_str)
        log.info("--- Prediction ---\n%s\n---", prediction)

        ok = send_telegram(prediction)
        sys.exit(0 if ok else 1)

    except ValueError as e:
        msg = f"Could not analyse {STOCK_TICKER}: {e}"
        log.error(msg)
        send_telegram(f"Error: {msg}")
        sys.exit(1)
    except Exception:
        log.error("Unexpected error:\n%s", traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
