"""
Daily Pre-Market Stock Briefing — Telegram Bot
Fetches live market data via yfinance, generates a briefing with Gemini,
and sends it over Telegram. Runs headlessly via GitHub Actions cron every weekday.
"""

import os
import sys
import logging
import traceback
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types
import yfinance as yf
import requests
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_MODEL       = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Market Data ───────────────────────────────────────────────────────────────

TICKERS = {
    "SPY":  "S&P 500 ETF",
    "QQQ":  "Nasdaq ETF",
    "NVDA": "NVIDIA",
    "AAPL": "Apple",
    "TSLA": "Tesla",
    "MSFT": "Microsoft",
    "AMZN": "Amazon",
}

def fetch_market_data() -> str:
    log.info("Fetching market data via yfinance...")
    lines = []
    for ticker, name in TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            info = t.fast_info
            price  = info.last_price
            prev   = info.previous_close
            change = ((price - prev) / prev * 100) if prev else 0
            arrow  = "up" if change >= 0 else "down"
            lines.append(f"{ticker} ({name}): ${price:.2f} {arrow} {abs(change):.2f}%")
        except Exception as e:
            log.warning("Could not fetch %s: %s", ticker, e)
            lines.append(f"{ticker}: data unavailable")

    data = "\n".join(lines)
    log.info("Market data fetched:\n%s", data)
    return data

# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a pre-market stock analyst and news briefer producing a concise daily morning message.
Use the live market data provided for prices. Use your knowledge for analyst calls, news, and outlook.
Total message must be readable in under 5 minutes — keep every line tight and scannable.
Do NOT use markdown, asterisks, or bold. Plain text only. Use the emoji prefix shown exactly.
No filler words. Be direct and specific.

Output EXACTLY this format — fill in the brackets, keep all section labels as-is, no extra lines:

📊 [Day, Month Date Year] | Pre-Market Briefing

— MARKET SNAPSHOT —
📈 SPY: $[price] [up/down] [%] | QQQ: $[price] [up/down] [%]
📉 NVDA: $[price] [up/down] [%] | TSLA: $[price] [up/down] [%]
🌍 Mood: [one word] — [max 10-word reason]

— TODAY'S OUTLOOK —
[2-3 sentences on what to expect for the trading day: sector rotation, catalysts, key levels]

— TOP ANALYST MOVES —
🟢 BUY: [Company] ($[TICKER]) | Now: $[current price] | Target: $[price] by [Month Year] | [one-line reason]
🟢 BUY: [Company] ($[TICKER]) | Now: $[current price] | Target: $[price] by [Month Year] | [one-line reason]
🔴 SELL: [Company] ($[TICKER]) | Now: $[current price] | Downside: $[price] by [Month Year] | [one-line reason]
🔴 SELL: [Company] ($[TICKER]) | Now: $[current price] | Downside: $[price] by [Month Year] | [one-line reason]

— HOT NEWS —
🇺🇸 [Trump/US policy headline — one sentence]
🌐 [World news headline — one sentence]
💼 [Market/earnings/macro headline — one sentence]

⚠️ Risk of the Day: [one sentence, max 15 words]"""

def build_user_prompt(market_data: str) -> str:
    return (
        f"Today is {datetime.now().strftime('%A, %B %d, %Y')}.\n\n"
        f"Live pre-market data:\n{market_data}\n\n"
        "Generate today's full pre-market briefing using the data and format above. "
        "Use real analyst calls and today's actual news headlines. "
        "Strict format only. No markdown. No extra lines."
    )

# ── Gemini ────────────────────────────────────────────────────────────────────

def fetch_briefing() -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set.")

    market_data = fetch_market_data()

    client = genai.Client(api_key=GEMINI_API_KEY)

    log.info("Calling Gemini (model=%s)...", GEMINI_MODEL)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_user_prompt(market_data),
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
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")

    url  = TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN)
    body = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
    }

    masked = f"...{TELEGRAM_CHAT_ID[-4:]}" if len(TELEGRAM_CHAT_ID) > 4 else TELEGRAM_CHAT_ID
    log.info("Sending Telegram message to chat %s...", masked)
    resp = requests.post(url, json=body, timeout=30)

    if resp.status_code == 200 and resp.json().get("ok"):
        log.info("Telegram message sent successfully.")
        return True

    log.error("Telegram error %s: %s", resp.status_code, resp.text)
    return False

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Briefing Job Start ===")
    try:
        message = fetch_briefing()
        log.info("--- Message ---\n%s\n---", message)
        ok = send_telegram(message)
        sys.exit(0 if ok else 1)
    except requests.HTTPError as e:
        log.error("HTTP error %s: %s", e.response.status_code, e.response.text[:300])
        sys.exit(1)
    except requests.Timeout:
        log.error("Request timed out.")
        sys.exit(1)
    except ValueError as e:
        log.error("Config error: %s", e)
        sys.exit(1)
    except Exception:
        log.error("Unexpected error:\n%s", traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
