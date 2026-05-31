"""
bot.py — Telegram Bot Listener
Polls Telegram for new messages every time it runs (triggered by GitHub Actions cron).
If a message looks like a stock ticker (e.g. AAPL, NVDA), it runs the full
technical analysis and replies directly to the sender.

Filters to messages received in the last 5 minutes to avoid reprocessing old ones.
"""

import os
import sys
import re
import time
import logging
import traceback
from pathlib import Path

import requests
from dotenv import load_dotenv

# Import prediction logic from predict.py
sys.path.insert(0, str(Path(__file__).parent))
from predict import (
    fetch_technical_data,
    format_tech_data,
    fetch_prediction,
    GEMINI_API_KEY,
    GEMINI_MODEL,
)

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_URL       = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# How far back to look for messages (seconds). Set to 6 min to overlap slightly.
LOOKBACK_SECONDS = 360

# ── Logging ───────────────────────────────────────────────────────────────────

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Telegram Helpers ──────────────────────────────────────────────────────────

def get_updates() -> list:
    """Fetch recent Telegram messages."""
    resp = requests.get(
        f"{TELEGRAM_URL}/getUpdates",
        params={"limit": 50, "timeout": 5},
        timeout=15,
    )
    if resp.status_code == 200 and resp.json().get("ok"):
        return resp.json().get("result", [])
    log.error("getUpdates failed: %s", resp.text)
    return []

def send_reply(chat_id: int, text: str):
    """Send a reply to a specific chat."""
    resp = requests.post(
        f"{TELEGRAM_URL}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )
    if resp.status_code == 200 and resp.json().get("ok"):
        log.info("Reply sent to chat %s", chat_id)
    else:
        log.error("Failed to send reply: %s", resp.text)

def send_typing(chat_id: int):
    """Show 'typing...' indicator while analysis runs."""
    requests.post(
        f"{TELEGRAM_URL}/sendChatAction",
        json={"chat_id": chat_id, "action": "typing"},
        timeout=10,
    )

# ── Ticker Validation ─────────────────────────────────────────────────────────

# Valid ticker: 1-5 uppercase letters, optionally preceded by $
TICKER_PATTERN = re.compile(r"^\$?([A-Z]{1,5})$")

def parse_ticker(text: str) -> str | None:
    """Return the ticker if the message looks like one, else None."""
    text = text.strip().upper()
    match = TICKER_PATTERN.match(text)
    return match.group(1) if match else None

# ── Help Message ──────────────────────────────────────────────────────────────

HELP_TEXT = """\
👋 Stock Analysis Bot

Send me any stock ticker and I'll reply with a full technical analysis including:
  • Today's estimated high & low
  • This week's high/low targets with entry & exit prices
  • Expected price path and recovery outlook

Examples:
  AAPL
  NVDA
  TSLA
  $MSFT

⚠️ Analysis takes ~30 seconds. Please wait after sending."""

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set.")
        sys.exit(1)
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not set.")
        sys.exit(1)

    log.info("=== Bot Poll Start ===")
    now = int(time.time())
    cutoff = now - LOOKBACK_SECONDS

    updates = get_updates()
    log.info("Fetched %d updates.", len(updates))

    processed = 0
    for update in updates:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue

        msg_time = msg.get("date", 0)
        if msg_time < cutoff:
            continue  # Too old — skip

        chat_id = msg["chat"]["id"]
        text    = msg.get("text", "").strip()
        sender  = msg["from"].get("first_name", "there")

        log.info("New message from %s (chat %s): '%s'", sender, chat_id, text)

        # Help command
        if text.lower() in ("/start", "/help", "help"):
            send_reply(chat_id, HELP_TEXT)
            processed += 1
            continue

        # Try to parse as a ticker
        ticker = parse_ticker(text)
        if not ticker:
            send_reply(
                chat_id,
                f"❓ '{text}' doesn't look like a ticker.\n\n"
                "Send a stock symbol like AAPL, NVDA, or TSLA.\n"
                "Type /help for instructions.",
            )
            processed += 1
            continue

        # Run analysis
        send_typing(chat_id)
        send_reply(chat_id, f"🔍 Analysing {ticker}... please wait ~30 seconds.")

        try:
            send_typing(chat_id)
            data       = fetch_technical_data(ticker)
            data_str   = format_tech_data(data)
            prediction = fetch_prediction(data_str)
            send_reply(chat_id, prediction)
        except ValueError as e:
            send_reply(chat_id, f"❌ Could not analyse {ticker}: {e}")
        except Exception:
            log.error("Unexpected error:\n%s", traceback.format_exc())
            send_reply(chat_id, f"❌ Something went wrong analysing {ticker}. Please try again.")

        processed += 1

    log.info("=== Bot Poll End — processed %d messages ===", processed)


if __name__ == "__main__":
    main()
