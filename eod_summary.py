"""
eod_summary.py — End-of-day performance check for morning picks.

Reads today's picks from a cached JSON file written by daily_scan.py,
re-fetches current (EOD) prices, compares against entry zones / stops / targets,
and sends a performance summary to Telegram.

If no cache file is found (e.g., weekend / holiday) it exits silently.
"""

import io
import sys
import json
import logging
import os
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

import yfinance as yf

from formatter      import format_eod_summary
from telegram_utils import send_messages

TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CACHE_FILE = os.path.join(os.path.dirname(__file__), "picks_cache.json")


def refresh_prices(picks: list) -> list:
    """Replace tech.current_price with the latest intraday price for each pick."""
    updated = []
    for p in picks:
        ticker = p["ticker"]
        try:
            info  = yf.Ticker(ticker).fast_info
            price = round(float(info.last_price), 2)
            p["tech"]["current_price"] = price
            log.info("  %-6s  EOD price: $%.2f", ticker, price)
        except Exception as e:
            log.warning("  %-6s  price refresh failed: %s", ticker, e)
        updated.append(p)
    return updated


def run_eod():
    if not os.path.exists(CACHE_FILE):
        log.info("No picks cache found at %s — nothing to summarise.", CACHE_FILE)
        return

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        picks = json.load(f)

    if not picks:
        log.info("Cache is empty — nothing to summarise.")
        return

    log.info("Loaded %d picks from cache. Refreshing prices …", len(picks))
    picks = refresh_prices(picks)

    date_str = datetime.now().strftime("%A, %B %d %Y")
    msg = format_eod_summary(picks, date_str=date_str)

    log.info("Sending EOD summary …")
    send_messages(TG_TOKEN, TG_CHAT_ID, msg)
    log.info("EOD summary sent.")


if __name__ == "__main__":
    run_eod()
