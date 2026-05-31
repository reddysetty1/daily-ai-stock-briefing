"""
telegram_utils.py — Shared Telegram delivery utilities.
Used by daily_scan.py, eod_summary.py, and any new module.
Existing briefing.py and predict.py keep their own send functions untouched.
"""

import os
import logging
import requests

log = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4096


def send_message(token: str, chat_id: str, text: str) -> bool:
    """Send a single Telegram message. Returns True on success."""
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=body, timeout=30)
        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        log.error("Telegram error %s: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.error("Telegram send exception: %s", e)
        return False


def split_message(text: str) -> list:
    """Split text into Telegram-safe chunks of <= 4096 chars, splitting on paragraph boundaries."""
    if len(text) <= TELEGRAM_MAX_CHARS:
        return [text]

    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = (current + "\n\n" + block).lstrip() if current else block
        if len(candidate) > TELEGRAM_MAX_CHARS:
            if current:
                parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def send_messages(token: str, chat_id: str, text: str) -> bool:
    """Send text, auto-splitting into multiple messages if needed."""
    chunks = split_message(text)
    ok = True
    for chunk in chunks:
        if not send_message(token, chat_id, chunk):
            ok = False
    return ok
