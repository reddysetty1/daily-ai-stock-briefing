"""
ai_watchlist_scanner.py — AI & High-Growth Watchlist Earnings Alert

Monitors a curated list of AI/high-growth stocks daily.
When any stock has earnings within 7 days, sends a Telegram alert
with a "Blowout Predictor" score — estimating how likely the quarter
is to be explosive based on:
  - Options implied move (market pricing in a big move?)
  - Revenue growth acceleration (getting faster each quarter?)
  - Analyst estimate revisions (raised in last 30 days = smart money signal)
  - Historical beat streak (does this stock consistently beat?)
  - Short interest (high short float = squeeze amplifier)
  - News sentiment (recent headlines positive or negative?)

Run daily at 8:00 AM ET via GitHub Actions.
"""

import os
import sys
import io
import logging
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

import yfinance as yf
import pandas as pd
import numpy as np

from telegram_utils import send_messages

TG_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Watchlist ─────────────────────────────────────────────────────────────────
# Add/remove tickers here. These are watched regardless of market cap or volume.
AI_WATCHLIST = {
    # AI Platform / Software
    "PLTR": "Palantir — AI Platform",
    "AI":   "C3.ai — Enterprise AI",
    "BBAI": "BigBear.ai — Defense AI",
    "SOUN": "SoundHound — Voice AI",
    # Semiconductors / AI Infrastructure
    "NVDA": "Nvidia — AI Chips",
    "AMD":  "AMD — AI Chips",
    "ARM":  "ARM Holdings — Chip Architecture",
    "AVGO": "Broadcom — AI Networking",
    "ANET": "Arista Networks — AI Data Center Networking",
    "MRVL": "Marvell — AI Data Center",
    "SMCI": "Super Micro — AI Servers",
    "ALAB": "Astera Labs — AI Connectivity",
    # Big Tech AI
    "META": "Meta — AI/Social",
    "GOOG": "Alphabet — AI/Search",
    "MSFT": "Microsoft — AI/Cloud",
    "AMZN": "Amazon — AI/Cloud",
    "AAPL": "Apple — AI/Consumer",
    # Robotics / Industrial AI
    "TSLA": "Tesla — AI/Robotics",
    "ACHR": "Archer Aviation — Air Mobility",
    "JOBY": "Joby Aviation — Air Mobility",
    # Cybersecurity AI
    "CRWD": "CrowdStrike — AI Security",
    "S":    "SentinelOne — AI Security",
    "ZS":   "Zscaler — AI Security",
    # High-Growth Tech (non-AI but momentum names)
    "SHOP": "Shopify — E-Commerce",
    "SNOW": "Snowflake — Data Cloud",
    "MDB":  "MongoDB — Database",
    "NET":  "Cloudflare — Edge Network",
    "DDOG": "Datadog — Observability",
    "BILL": "Bill.com — Fintech AI",
    "COIN": "Coinbase — Crypto",
    "HOOD": "Robinhood — Retail Fintech",
}

DAYS_AHEAD        = 7   # scan window — fetch all stocks within this many days
URGENT_DAYS       = 2   # always alert when earnings this close
ALERT_SCORE_MIN   = 65  # always alert when blowout score >= this (even if 3-7 days out)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_earnings_date(t) -> date | None:
    """Return next upcoming earnings date or None."""
    try:
        cal = t.calendar
        if cal is not None and "Earnings Date" in cal:
            ed = cal["Earnings Date"]
            if hasattr(ed, "__iter__"):
                for d in ed:
                    d = pd.Timestamp(d).date()
                    if d >= date.today():
                        return d
            else:
                d = pd.Timestamp(ed).date()
                if d >= date.today():
                    return d
    except Exception:
        pass
    # Fallback: earnings_dates index
    try:
        ed_df = t.earnings_dates
        if ed_df is not None and not ed_df.empty:
            future = [
                pd.Timestamp(d).date()
                for d in ed_df.index
                if pd.Timestamp(d).date() >= date.today()
            ]
            if future:
                return min(future)
    except Exception:
        pass
    return None


def _beat_streak(t) -> tuple[int, int, float]:
    """Returns (beats, total_quarters, beat_rate) from earnings history."""
    try:
        eh = t.earnings_history
        if eh is None or eh.empty:
            return 0, 0, 0.0
        # Keep last 8 quarters
        recent = eh.dropna(subset=["epsEstimate", "epsActual"]).tail(8)
        if recent.empty:
            return 0, 0, 0.0
        beats = int((recent["epsActual"] >= recent["epsEstimate"]).sum())
        total = len(recent)
        # Consecutive beat streak from most recent
        streak = 0
        for _, row in recent.iloc[::-1].iterrows():
            if row["epsActual"] >= row["epsEstimate"]:
                streak += 1
            else:
                break
        return streak, total, round(beats / total, 2) if total else 0.0
    except Exception:
        return 0, 0, 0.0


def _revenue_acceleration(t) -> float | None:
    """
    Returns revenue growth acceleration: latest YoY growth minus prior YoY growth.
    Positive = growth is speeding up (bullish signal).
    """
    try:
        fin = t.financials  # annual, columns = dates
        if fin is None or fin.empty or "Total Revenue" not in fin.index:
            return None
        rev = fin.loc["Total Revenue"].dropna().sort_index()
        if len(rev) < 3:
            return None
        # YoY growth for last two years
        g1 = (rev.iloc[-1] - rev.iloc[-2]) / abs(rev.iloc[-2]) * 100
        g2 = (rev.iloc[-2] - rev.iloc[-3]) / abs(rev.iloc[-3]) * 100
        return round(float(g1 - g2), 1)
    except Exception:
        return None


def _implied_move(t) -> float | None:
    """
    Estimate options-implied earnings move from nearest expiry straddle.
    Returns % expected move or None.
    """
    try:
        exps = t.options
        if not exps:
            return None
        price = float(t.fast_info.last_price)
        if price <= 0:
            return None
        # Use nearest expiry
        chain = t.option_chain(exps[0])
        calls = chain.calls
        puts  = chain.puts
        # ATM straddle: find strike closest to current price
        atm_strike = calls.iloc[(calls["strike"] - price).abs().argsort()].iloc[0]["strike"]
        call_row = calls[calls["strike"] == atm_strike]
        put_row  = puts[puts["strike"] == atm_strike]
        if call_row.empty or put_row.empty:
            return None
        call_mid = float((call_row["bid"].values[0] + call_row["ask"].values[0]) / 2)
        put_mid  = float((put_row["bid"].values[0] + put_row["ask"].values[0]) / 2)
        straddle_cost = call_mid + put_mid
        implied_pct   = round(straddle_cost / price * 100, 1)
        return implied_pct
    except Exception:
        return None


def _estimate_revisions(t) -> str:
    """
    Check if analysts raised EPS estimates recently.
    Returns 'RAISED', 'CUT', or 'FLAT'.
    """
    try:
        ud = t.upgrades_downgrades
        if ud is None or ud.empty:
            return "FLAT"
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=30)
        recent = ud[ud.index >= cutoff]
        if recent.empty:
            return "FLAT"
        grades = recent["ToGrade"].str.lower()
        upgrades   = grades.str.contains("buy|outperform|overweight|strong buy", na=False).sum()
        downgrades = grades.str.contains("sell|underperform|underweight|reduce", na=False).sum()
        if upgrades > downgrades:
            return "RAISED"
        elif downgrades > upgrades:
            return "CUT"
        return "FLAT"
    except Exception:
        return "FLAT"


def _short_interest(t) -> float | None:
    """Returns short float % or None."""
    try:
        info = t.info
        short_float = info.get("shortPercentOfFloat")
        if short_float:
            return round(float(short_float) * 100, 1)
    except Exception:
        pass
    return None


def _recent_news_tone(t) -> tuple[int, int]:
    """Returns (positive_count, total_count) from recent headlines."""
    pos_words = {
        "beat", "surge", "record", "strong", "growth", "upgrade", "buy",
        "outperform", "raise", "bullish", "profit", "deal", "wins", "contract",
        "momentum", "breakthrough", "exceed", "rally", "upside", "optimistic",
        "accelerating", "booming", "demand", "robust", "above", "boost",
    }
    neg_words = {
        "miss", "decline", "falls", "drops", "warning", "downgrade", "sell",
        "bearish", "loss", "weak", "slump", "lawsuit", "probe", "fraud",
        "layoff", "disappoint", "concern", "risk", "pressure", "headwind",
        "below", "shortfall", "tumbles", "investigation", "penalty", "debt",
    }
    try:
        articles = t.news or []
        pos, total = 0, 0
        for a in articles[:10]:
            title = (a.get("content", {}).get("title") or a.get("title") or "").lower()
            if not title:
                continue
            words = set(title.split())
            total += 1
            if words & pos_words:
                pos += 1
            elif words & neg_words:
                pass
        return pos, total
    except Exception:
        return 0, 0


# ── Blowout Predictor Score ───────────────────────────────────────────────────

def blowout_score(
    beat_streak: int,
    beat_rate: float,
    rev_accel: float | None,
    implied_move: float | None,
    revisions: str,
    short_pct: float | None,
    news_pos: int,
    news_total: int,
) -> tuple[int, list[str]]:
    """
    Score 0-100 estimating probability of an explosive earnings beat.
    Returns (score, list_of_signal_lines).
    """
    score   = 0
    signals = []

    # 1. Beat streak (up to 30 pts)
    if beat_streak >= 8:
        score += 30; signals.append(f"🔥 Beat streak: {beat_streak} consecutive beats — machine")
    elif beat_streak >= 6:
        score += 24; signals.append(f"✅ Beat streak: {beat_streak} consecutive beats")
    elif beat_streak >= 4:
        score += 18; signals.append(f"✅ Beat streak: {beat_streak} consecutive beats")
    elif beat_streak >= 2:
        score += 10; signals.append(f"📊 Beat streak: {beat_streak} beats in a row")
    else:
        signals.append(f"⚠️ Beat streak: {beat_streak} — inconsistent")

    # 2. Beat rate (up to 15 pts)
    if beat_rate >= 0.88:
        score += 15; signals.append(f"🔥 Beat rate: {beat_rate*100:.0f}% — elite")
    elif beat_rate >= 0.75:
        score += 10; signals.append(f"✅ Beat rate: {beat_rate*100:.0f}% — strong")
    elif beat_rate >= 0.60:
        score += 5;  signals.append(f"📊 Beat rate: {beat_rate*100:.0f}% — ok")
    else:
        signals.append(f"⚠️ Beat rate: {beat_rate*100:.0f}% — weak")

    # 3. Revenue acceleration (up to 20 pts)
    if rev_accel is not None:
        if rev_accel >= 30:
            score += 20; signals.append(f"🚀 Revenue growth accelerating +{rev_accel:.0f}pp YoY — exponential")
        elif rev_accel >= 15:
            score += 14; signals.append(f"🔥 Revenue accelerating +{rev_accel:.0f}pp YoY")
        elif rev_accel >= 5:
            score += 8;  signals.append(f"✅ Revenue growing +{rev_accel:.0f}pp faster YoY")
        elif rev_accel >= 0:
            score += 3;  signals.append(f"📊 Revenue growth stable (+{rev_accel:.0f}pp)")
        else:
            signals.append(f"⚠️ Revenue growth slowing ({rev_accel:.0f}pp deceleration)")
    else:
        signals.append("❓ Revenue acceleration: no data")

    # 4. Options implied move (up to 15 pts — high IV = market expects fireworks)
    if implied_move is not None:
        if implied_move >= 20:
            score += 15; signals.append(f"⚡ Options imply ±{implied_move:.0f}% move — massive expectation")
        elif implied_move >= 12:
            score += 10; signals.append(f"⚡ Options imply ±{implied_move:.0f}% move — big expectation")
        elif implied_move >= 7:
            score += 5;  signals.append(f"📊 Options imply ±{implied_move:.0f}% move — moderate")
        else:
            signals.append(f"📊 Options imply ±{implied_move:.0f}% move — low volatility priced in")
    else:
        signals.append("❓ Options data: unavailable")

    # 5. Analyst estimate revisions (up to 10 pts)
    if revisions == "RAISED":
        score += 10; signals.append("✅ Analysts raised estimates this month — smart money is bullish")
    elif revisions == "CUT":
        signals.append("⚠️ Analysts cut estimates this month — expectations lowered")
    else:
        signals.append("📊 Analyst estimates: no major revisions")

    # 6. Short squeeze potential (up to 10 pts)
    if short_pct is not None:
        if short_pct >= 20:
            score += 10; signals.append(f"💥 Short interest: {short_pct:.0f}% float — MASSIVE squeeze risk if beat")
        elif short_pct >= 12:
            score += 7;  signals.append(f"⚡ Short interest: {short_pct:.0f}% float — squeeze possible on beat")
        elif short_pct >= 7:
            score += 3;  signals.append(f"📊 Short interest: {short_pct:.0f}% float — some squeeze fuel")
        else:
            signals.append(f"📊 Short interest: {short_pct:.0f}% float — low")
    else:
        signals.append("❓ Short interest: no data")

    # Clamp to 100
    return min(score, 100), signals


# ── Verdict from score ────────────────────────────────────────────────────────

def _verdict(score: int) -> str:
    if score >= 80:
        return "🔥🔥 EXTREME BLOWOUT POTENTIAL — high conviction play"
    elif score >= 65:
        return "🔥 HIGH BLOWOUT POTENTIAL — strong setup"
    elif score >= 50:
        return "⚡ MODERATE POTENTIAL — worth watching"
    elif score >= 35:
        return "📊 LOW-MODERATE — proceed with caution"
    else:
        return "😐 WEAK SETUP — skip or very small size"


# ── Per-stock analysis ────────────────────────────────────────────────────────

def analyze_watchlist_stock(ticker: str, label: str) -> dict | None:
    """Fetch all signals for one watchlist stock. Returns None if no earnings soon."""
    try:
        t = yf.Ticker(ticker)
        ed = _get_earnings_date(t)
        if ed is None:
            log.info("  %-6s  no earnings date found", ticker)
            return None
        days_away = (ed - date.today()).days
        if days_away < 0 or days_away > DAYS_AHEAD:
            log.info("  %-6s  earnings %s (%d days) — outside window", ticker, ed, days_away)
            return None

        log.info("  %-6s  earnings %s (%d days) — INSIDE WINDOW, analyzing…", ticker, ed, days_away)

        price = 0.0
        try:
            price = round(float(t.fast_info.last_price), 2)
        except Exception:
            pass

        streak, quarters, rate = _beat_streak(t)
        rev_accel   = _revenue_acceleration(t)
        impl_move   = _implied_move(t)
        revisions   = _estimate_revisions(t)
        short_pct   = _short_interest(t)
        news_pos, news_total = _recent_news_tone(t)

        bp_score, signals = blowout_score(
            beat_streak  = streak,
            beat_rate    = rate,
            rev_accel    = rev_accel,
            implied_move = impl_move,
            revisions    = revisions,
            short_pct    = short_pct,
            news_pos     = news_pos,
            news_total   = news_total,
        )

        return {
            "ticker":      ticker,
            "label":       label,
            "price":       price,
            "earnings_date": ed,
            "days_away":   days_away,
            "streak":      streak,
            "quarters":    quarters,
            "beat_rate":   rate,
            "rev_accel":   rev_accel,
            "impl_move":   impl_move,
            "revisions":   revisions,
            "short_pct":   short_pct,
            "news_pos":    news_pos,
            "news_total":  news_total,
            "bp_score":    bp_score,
            "signals":     signals,
        }
    except Exception as e:
        log.warning("  %-6s  FAILED: %s", ticker, e)
        return None


# ── Message formatter ─────────────────────────────────────────────────────────

def format_alert(results: list[dict], today: date) -> str:
    high   = [r for r in results if r["bp_score"] >= 65]
    medium = [r for r in results if 35 <= r["bp_score"] < 65]
    low    = [r for r in results if r["bp_score"] < 35]

    lines = [
        f"🤖 AI WATCHLIST — EARNINGS ALERT",
        f"📅 {today.strftime('%A, %b %d %Y')}",
        f"{'━'*34}",
        f"{len(results)} watchlist stock(s) reporting in next {DAYS_AHEAD} days",
        "",
    ]

    def _section(stocks: list[dict], header: str):
        if not stocks:
            return
        lines.append(header)
        for r in stocks:
            day_label = "TOMORROW" if r["days_away"] == 1 else (
                "TODAY (AH)" if r["days_away"] == 0 else
                f"in {r['days_away']} days ({r['earnings_date'].strftime('%b %d')})"
            )
            lines += [
                "",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"🎯 {r['ticker']} — {r['label']}",
                f"   Price: ${r['price']}  |  Earnings: {day_label}",
                f"   Blowout Score: {r['bp_score']}/100  {_verdict(r['bp_score'])}",
                "",
                "   SIGNALS:",
            ]
            for sig in r["signals"]:
                lines.append(f"   {sig}")

            # Strategy recommendation
            lines.append("")
            if r["bp_score"] >= 65:
                if r["days_away"] <= 1:
                    lines.append("   ⚡ STRATEGY: Very little time — if you believe in this, enter TODAY before close.")
                    lines.append("   Hold through earnings (Strategy B). Risk only what you can lose 100% of.")
                else:
                    lines.append(f"   ⚡ STRATEGY: You have {r['days_away']} days. Consider entering now.")
                    lines.append("   Strategy A: sell day before earnings (no binary risk).")
                    lines.append("   Strategy B: hold through for the big move. Start small, add if it runs.")
            elif r["bp_score"] >= 35:
                lines.append("   📊 STRATEGY: Watchlist only. Wait for confirmation — don't chase.")
                lines.append("   If it shows strength 2 days before earnings, small entry ok.")
            else:
                lines.append("   😐 STRATEGY: Skip. Signals too weak for an earnings play.")

    _section(sorted(high, key=lambda x: -x["bp_score"]), "🔥 HIGH POTENTIAL PLAYS:")
    _section(sorted(medium, key=lambda x: -x["bp_score"]), "⚡ MODERATE PLAYS:")
    _section(sorted(low, key=lambda x: -x["bp_score"]), "📊 LOW CONVICTION (watchlist only):")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ This is NOT financial advice.",
        "Blowout scores estimate probability only — earnings are unpredictable.",
        "Never risk more than you can afford to lose entirely.",
    ]
    return "\n".join(lines)


def format_no_alerts(today: date) -> str:
    return (
        f"🤖 AI WATCHLIST — Daily Check\n"
        f"📅 {today.strftime('%A, %b %d %Y')}\n"
        f"{'━'*34}\n"
        f"No watchlist stocks reporting earnings in the next {DAYS_AHEAD} days.\n"
        f"Monitoring: {', '.join(AI_WATCHLIST.keys())}"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = date.today()
    log.info("AI Watchlist Scanner — %s", today)
    log.info("Checking %d stocks for earnings within %d days…", len(AI_WATCHLIST), DAYS_AHEAD)

    results = []
    for ticker, label in AI_WATCHLIST.items():
        r = analyze_watchlist_stock(ticker, label)
        if r:
            results.append(r)

    log.info("Found %d stocks with upcoming earnings.", len(results))

    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("No Telegram credentials — printing to stdout only.")
        print(format_alert(results, today) if results else format_no_alerts(today))
        return

    if results:
        results.sort(key=lambda x: -x["bp_score"])

        # Only send alert if something is urgent or high-conviction
        # Urgent = earnings within URGENT_DAYS OR blowout score >= ALERT_SCORE_MIN
        urgent = [r for r in results if r["days_away"] <= URGENT_DAYS or r["bp_score"] >= ALERT_SCORE_MIN]

        if urgent:
            msg = format_alert(urgent, today)
            log.info("Sending URGENT alert (%d stocks) to Telegram…", len(urgent))
            send_messages(TG_TOKEN, TG_CHAT_ID, msg)
            log.info("Alert sent.")
        else:
            # Stocks found but nothing urgent — log only, no Telegram noise
            log.info("Stocks on radar but nothing urgent yet: %s",
                     ", ".join(f"{r['ticker']}({r['days_away']}d,{r['bp_score']}pt)" for r in results))
    else:
        log.info("No watchlist stocks reporting within %d days — no alert.", DAYS_AHEAD)

    log.info("Done.")


if __name__ == "__main__":
    main()
