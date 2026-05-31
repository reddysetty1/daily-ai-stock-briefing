"""
daily_scan.py — Pre-market stock selection and trade planning pipeline.

Flow:
  1. Load config.json
  2. Fetch SPY/QQQ/IWM market context
  3. Score all universe stocks (tech + fundamentals)
  4. Select top N picks above min_score
  5. Generate entry/exit plans + position sizes for each pick
  6. Call Gemini for a short narrative per pick
  7. Send ranked summary + per-stock detail messages to Telegram
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

# ── Third-party ───────────────────────────────────────────────────────────────
import yfinance as yf
from google import genai
from google.genai import types

# ── Local modules ─────────────────────────────────────────────────────────────
from tech_analysis  import fetch_full_technical
from fundamentals   import fetch_fundamentals
from scoring        import score_stock
from signals        import detect_setup, generate_entry_plan, generate_exit_plan
from risk           import calculate_position_size, assess_setup_quality
from formatter      import format_ranked_summary, format_stock_detail
from telegram_utils import send_messages

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_PATH  = os.path.join(os.path.dirname(__file__), "config.json")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_KEY   = os.getenv("GEMINI_API_KEY", "")
TG_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


# ── Market context ────────────────────────────────────────────────────────────

def fetch_market_context(etf_list: list) -> dict:
    """Fetch price + day_change_pct for SPY / QQQ / IWM."""
    result = {}
    for ticker in etf_list:
        try:
            info = yf.Ticker(ticker).fast_info
            price = round(float(info.last_price), 2)
            prev  = float(info.previous_close)
            chg   = round((price - prev) / prev * 100, 2) if prev else 0.0
            result[ticker] = {"price": price, "day_change_pct": chg}
            log.info("Market %s  $%.2f  %+.2f%%", ticker, price, chg)
        except Exception as e:
            log.warning("Market fetch failed for %s: %s", ticker, e)
            result[ticker] = {"price": 0.0, "day_change_pct": 0.0}
    return result


# ── Gemini narrative ──────────────────────────────────────────────────────────

def fetch_narrative(client, ticker: str, tech: dict, fund: dict,
                    setup: str, ep: dict, ex: dict) -> str:
    """Ask Gemini for a 2-3 sentence trade rationale."""
    prompt = (
        f"You are an institutional equity analyst. Write a concise 2-3 sentence "
        f"trade rationale for {ticker} based on the data below. Focus on the key "
        f"reason this setup is interesting today and the main risk to the trade.\n\n"
        f"Setup: {setup}\n"
        f"Trend: {tech.get('trend')}\n"
        f"RSI(14): {tech.get('rsi')}\n"
        f"MACD: {'Bullish' if tech.get('macd', 0) > tech.get('macd_signal', 0) else 'Bearish'}\n"
        f"Volume vs avg: {tech.get('vol_pct', 0):+.0f}%\n"
        f"Price attractiveness: {tech.get('attractiveness')}\n"
        f"Entry style: {ep.get('style')}\n"
        f"Entry zone: ${ep.get('entry_low')} – ${ep.get('entry_high')}\n"
        f"Stop: ${ex.get('stop')} | T1: ${ex.get('t1')} | R:R {ex.get('rr1')}:1\n"
        f"Revenue growth: {fund.get('revenue_growth')}\n"
        f"EPS growth: {fund.get('earnings_growth')}\n"
        f"P/E: {fund.get('trailing_pe')}\n"
        f"Sector: {fund.get('sector')}\n"
        f"Keep it under 60 words. No bullet points. Plain text only."
    )
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=120,
            ),
        )
        return resp.text.strip()
    except Exception as e:
        log.warning("Gemini narrative failed for %s: %s", ticker, e)
        return ""


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_scan():
    cfg        = load_config()
    universe   = cfg["universe"]          # {ticker: name}
    sector_map = cfg["sector_map"]        # {ticker: ETF}
    weights    = cfg["weights"]
    sel_cfg    = cfg["selection"]
    risk_cfg   = cfg["risk"]

    etf_list   = list(cfg["market_etfs"].keys())   # [SPY, QQQ, IWM]
    market_data = fetch_market_context(etf_list)

    gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

    # ── Step 1: Score every stock ─────────────────────────────────────────────
    scored = []
    tickers = list(universe.keys())
    log.info("Scanning %d stocks …", len(tickers))

    for ticker in tickers:
        try:
            tech = fetch_full_technical(ticker)
            fund = fetch_fundamentals(ticker)
            final_score, breakdown = score_stock(
                ticker, tech, fund, market_data, sector_map, weights
            )
            scored.append({
                "ticker":    ticker,
                "score":     final_score,
                "tech":      tech,
                "fund":      fund,
                "breakdown": breakdown,
            })
            log.info("  %-6s  score=%.1f  trend=%s  rsi=%.0f",
                     ticker, final_score, tech.get("trend", "?"), tech.get("rsi", 0))
        except Exception as e:
            log.warning("  %-6s  FAILED: %s", ticker, e)

    # Sort best-first
    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── Step 2: Filter and select top N ───────────────────────────────────────
    min_score = sel_cfg["min_score"]
    top_n_min = sel_cfg["top_n_min"]
    top_n_max = sel_cfg["top_n_max"]

    eligible = [s for s in scored if s["score"] >= min_score]
    picks_raw = eligible[:top_n_max]

    # Ensure we always send at least top_n_min even if below threshold
    if len(picks_raw) < top_n_min:
        picks_raw = scored[:top_n_min]

    log.info("Selected %d picks (eligible above %.0f: %d)",
             len(picks_raw), min_score, len(eligible))

    # ── Step 3: Build full trade plans ────────────────────────────────────────
    picks = []
    for item in picks_raw:
        ticker = item["ticker"]
        tech   = item["tech"]
        fund   = item["fund"]

        setup = detect_setup(tech)
        ep    = generate_entry_plan(tech, setup)
        ex    = generate_exit_plan(tech, ep, setup, risk_cfg)

        # Position sizing
        entry_price = ep.get("entry_low") or tech["current_price"]
        pos = calculate_position_size(
            entry_price          = entry_price,
            stop_price           = ex["stop"],
            account_size         = risk_cfg["account_size"],
            risk_pct             = risk_cfg["risk_pct_per_trade"],
            max_position_pct     = risk_cfg["max_position_pct"],
        )

        # Quality assessment
        quality, flags = assess_setup_quality(
            ex["rr1"], ex["rr2"],
            risk_cfg["min_risk_reward"],
            setup, tech
        )

        # Gemini narrative
        narrative = ""
        if gemini_client:
            narrative = fetch_narrative(gemini_client, ticker, tech, fund, setup, ep, ex)

        picks.append({
            "ticker":    ticker,
            "score":     item["score"],
            "setup":     setup,
            "tech":      tech,
            "fund":      fund,
            "breakdown": item["breakdown"],
            "quality":   quality,
            "flags":     flags,
            "narrative": narrative,
            "entry": {
                "entry_plan":  ep,
                "exit_plan":   ex,
                "position":    pos,
            },
        })

    # ── Step 4: Format and send ───────────────────────────────────────────────
    date_str = datetime.now().strftime("%A, %B %d %Y")

    summary_msg = format_ranked_summary(
        picks         = picks,
        market_data   = market_data,
        total_scanned = len(tickers),
        date_str      = date_str,
    )

    log.info("Sending ranked summary …")
    send_messages(TG_TOKEN, TG_CHAT_ID, summary_msg)

    for p in picks:
        detail_msg = format_stock_detail(p, account_size=risk_cfg["account_size"])
        log.info("Sending detail for %s …", p["ticker"])
        send_messages(TG_TOKEN, TG_CHAT_ID, detail_msg)

    # ── Step 5: Cache picks for EOD summary ──────────────────────────────────
    cache_path = os.path.join(os.path.dirname(__file__), "picks_cache.json")
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(picks, f, default=str)
        log.info("Picks cached to %s", cache_path)
    except Exception as e:
        log.warning("Could not write picks cache: %s", e)

    log.info("Daily scan complete. %d picks sent.", len(picks))


if __name__ == "__main__":
    run_scan()
