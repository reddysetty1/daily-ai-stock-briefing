"""
test_run.py — Preview the Telegram message without sending it.
Run this locally to verify your API key and see the exact message format.

Usage:
    python test_run.py           # preview only, no Telegram sent
    python test_run.py --send    # preview AND send the Telegram message
"""

import sys
import io
from pathlib import Path

# Force UTF-8 output on Windows to handle emoji in the briefing
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

from briefing import (
    fetch_briefing,
    send_telegram,
    GEMINI_API_KEY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

SEND = "--send" in sys.argv

print("\n" + "=" * 52)
print("  Daily Briefing - Test Run")
print("=" * 52)
print(f"  Gemini Key     : {'SET [OK]' if GEMINI_API_KEY else 'MISSING [!]'}")
print(f"  Telegram Token : {'SET [OK]' if TELEGRAM_BOT_TOKEN else 'MISSING [!]'}")
print(f"  Telegram Chat  : {'SET [OK]' if TELEGRAM_CHAT_ID else 'MISSING [!]'}")
print(f"  Mode           : {'SEND' if SEND else 'PREVIEW ONLY'}")
print("-" * 52)

if not GEMINI_API_KEY:
    print("\n  ERROR: Set GEMINI_API_KEY in your .env file.")
    sys.exit(1)

print("\n  Fetching market data + generating briefing...\n")

try:
    message = fetch_briefing()

    print("  +-- Telegram Message Preview -------------------+")
    for line in message.splitlines():
        print(f"  |  {line}")
    print("  +-----------------------------------------------+")
    print(f"\n  Characters : {len(message)}")

    if SEND:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("\n  ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to send.")
            sys.exit(1)
        print("\n  Sending Telegram message...")
        ok = send_telegram(message)
        print("  Sent successfully [OK]" if ok else "  Send failed [!]")
    else:
        print("\n  (Dry run -- pass --send to actually deliver the message)")

    print("=" * 52 + "\n")

except Exception as e:
    print(f"\n  ERROR: {e}")
    sys.exit(1)
