"""
earnings_scanner.py — Pre-earnings opportunity scanner.

Finds stocks in the S&P 500 / NASDAQ 100 reporting earnings in the
next 1-5 trading days, then sends a comprehensive trade brief to
Telegram for each high-quality play.

Two strategies per play:
  A) Pre-earnings run  — buy now, sell BEFORE announcement (no binary risk)
  B) Hold through      — buy now, hold through announcement (higher R:R)

Run daily at ~5:30 AM PST via GitHub Actions.
"""

import os, json, logging, sys, io
from datetime import datetime, timedelta, date
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

GEMINI_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
TG_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Thresholds ─────────────────────────────────────────────────────────────────
MIN_BEAT_RATE     = 0.55    # at least 55% historical beat rate
MIN_QUARTERS      = 3       # need at least 3 quarters of history
DAYS_AHEAD        = 5       # scan earnings within next 5 trading days
MIN_AVG_VOLUME    = 500_000 # liquidity filter
MIN_MARKET_CAP    = 2_000_000_000   # $2B — earnings plays need size
MAX_WORKERS       = 20      # parallel yfinance calls

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)


# ── Universe ───────────────────────────────────────────────────────────────────

def _get_universe():
    """Load the full universe, filtered to liquid large-caps for earnings plays."""
    try:
        from universe import get_universe
        full = get_universe()
        # For earnings plays we want larger, more liquid names
        return list(full.keys())
    except Exception:
        # Fallback to config universe
        return list(CONFIG.get("universe", {}).keys())


# ── Earnings data helpers ──────────────────────────────────────────────────────

def _get_earnings_date(t):
    """Return (date, timing_str) for next earnings. timing: BMO/AMC/Unknown."""
    try:
        cal = t.calendar
        if cal is None:
            return None, "Unknown"
        dates = cal.get("Earnings Date", [])
        if not hasattr(dates, "__iter__") or isinstance(dates, str):
            dates = [dates]
        dates = [d for d in dates if d is not None]
        if not dates:
            return None, "Unknown"
        ed = pd.Timestamp(dates[0]).date()
        return ed, "Unknown"
    except Exception:
        return None, "Unknown"


def _get_earnings_history(t):
    """
    Returns list of dicts {estimated, actual, surprise_pct, beat}
    Most recent first. Up to 8 quarters.
    """
    records = []
    try:
        hist = t.earnings_history
        if hist is None or (hasattr(hist, "empty") and hist.empty):
            return records
        for _, row in hist.iterrows():
            est  = row.get("epsEstimate")
            act  = row.get("epsActual")
            surp = row.get("surprisePercent")
            if est is None or act is None:
                continue
            est, act = float(est), float(act)
            if surp is not None:
                surp_pct = float(surp) * 100 if abs(float(surp)) < 2 else float(surp)
            else:
                surp_pct = (act - est) / abs(est) * 100 if est != 0 else 0.0
            records.append({
                "estimated":    round(est, 3),
                "actual":       round(act, 3),
                "surprise_pct": round(surp_pct, 1),
                "beat":         act > est,
            })
    except Exception as e:
        log.debug("earnings_history error: %s", e)
    return records[:8]


def _get_post_earnings_moves(t):
    """
    Calculate historical post-earnings % price moves.
    Returns list of floats, most recent first.
    """
    moves = []
    try:
        ed_df = t.earnings_dates
        if ed_df is None or (hasattr(ed_df, "empty") and ed_df.empty):
            return moves
        hist = t.history(period="2y", interval="1d")
        if hist.empty:
            return moves

        past = [
            pd.Timestamp(d).date()
            for d in ed_df.index
            if pd.Timestamp(d).date() < date.today()
        ][:8]

        for ed in past:
            try:
                before_rows = hist[hist.index.date < ed]
                after_rows  = hist[hist.index.date >= ed]
                if before_rows.empty or after_rows.empty:
                    continue
                close_before = float(before_rows.iloc[-1]["Close"])
                # Use close on day of or next day (handles AMC reports)
                close_after  = float(after_rows.iloc[0]["Close"])
                if close_before > 0:
                    moves.append(round((close_after - close_before) / close_before * 100, 1))
            except Exception:
                continue
    except Exception as e:
        log.debug("post_earnings_moves error: %s", e)
    return moves


def _get_pre_run_momentum(t):
    """% price change over last 5 trading days — is smart money already buying?"""
    try:
        hist = t.history(period="10d", interval="1d")
        if len(hist) >= 5:
            c5  = float(hist["Close"].iloc[-5])
            now = float(hist["Close"].iloc[-1])
            return round((now - c5) / c5 * 100, 1) if c5 > 0 else 0.0
    except Exception:
        pass
    return 0.0


def _get_expected_move(t, earnings_date):
    """
    Options-implied expected move ≈ ATM straddle price / current price.
    Returns float (%) or None if options unavailable.
    """
    try:
        expirations = t.options
        if not expirations:
            return None
        # Find first expiry on or after earnings date
        target_exp = next(
            (e for e in expirations
             if datetime.strptime(e, "%Y-%m-%d").date() >= earnings_date),
            None
        )
        if not target_exp:
            return None
        chain = t.option_chain(target_exp)
        price = float(t.fast_info.last_price)
        calls, puts = chain.calls.copy(), chain.puts.copy()
        calls["dist"] = abs(calls["strike"] - price)
        puts["dist"]  = abs(puts["strike"]  - price)
        atm_call = float(calls.nsmallest(1, "dist").iloc[0]["lastPrice"])
        atm_put  = float(puts.nsmallest(1, "dist").iloc[0]["lastPrice"])
        return round((atm_call + atm_put) / price * 100, 1)
    except Exception:
        return None


def _get_avg_pre_run(t):
    """
    Average % gain in the 5 days BEFORE each past earnings announcement.
    Tells us: does this stock typically run up into earnings?
    """
    runs = []
    try:
        ed_df = t.earnings_dates
        if ed_df is None or (hasattr(ed_df, "empty") and ed_df.empty):
            return 0.0
        hist = t.history(period="2y", interval="1d")
        if hist.empty:
            return 0.0

        past = [
            pd.Timestamp(d).date()
            for d in ed_df.index
            if pd.Timestamp(d).date() < date.today()
        ][:6]

        for ed in past:
            try:
                before = hist[hist.index.date < ed]
                if len(before) < 5:
                    continue
                close_5ago = float(before.iloc[-5]["Close"])
                close_day_before = float(before.iloc[-1]["Close"])
                if close_5ago > 0:
                    runs.append((close_day_before - close_5ago) / close_5ago * 100)
            except Exception:
                continue
    except Exception:
        pass
    return round(float(np.mean(runs)), 1) if runs else 0.0


# ── Scoring ────────────────────────────────────────────────────────────────────

def _score_play(beat_rate, n_quarters, avg_surprise, avg_up, pre_momentum, tech_score):
    """Score an earnings play 0–100."""
    s = 0.0
    # Beat consistency (0–35)
    s += beat_rate * 35
    # Sample size confidence (0–10)
    s += min(10, n_quarters * 1.5)
    # Average EPS surprise magnitude (0–20)
    s += min(20, max(0, avg_surprise) * 0.8)
    # Average up-move size (0–20)
    s += min(20, max(0, avg_up) * 1.2)
    # Pre-earnings momentum — sweet spot is 2–8% (0–10)
    if 2 <= pre_momentum <= 8:
        s += 10
    elif 0 <= pre_momentum < 2:
        s += 5
    elif 8 < pre_momentum <= 15:
        s += 3   # running but not crazy
    # Technical quality (0–5)
    s += min(5, tech_score / 20)
    return round(min(100, s), 1)


# ── Per-ticker analysis ────────────────────────────────────────────────────────

def _analyze_one(ticker, today, cutoff):
    """
    Full earnings play analysis for a single ticker.
    Returns a play dict or None if not qualifying.
    """
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info

        # Basic liquidity / size gate
        avg_vol    = float(fi.three_month_average_volume or 0)
        mkt_cap    = float(fi.market_cap or 0)
        price      = float(fi.last_price or 0)
        prev_close = float(fi.previous_close or price)
        day_chg    = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0

        if avg_vol < MIN_AVG_VOLUME:
            return None
        if mkt_cap < MIN_MARKET_CAP:
            return None
        if price < 5:
            return None

        # Earnings date check
        earnings_dt, timing = _get_earnings_date(t)
        if earnings_dt is None:
            return None
        if not (today < earnings_dt <= cutoff):
            return None

        days_away = (earnings_dt - today).days

        # Earnings history
        history = _get_earnings_history(t)
        if len(history) < MIN_QUARTERS:
            return None

        beat_count = sum(1 for r in history if r["beat"])
        beat_rate  = beat_count / len(history)
        if beat_rate < MIN_BEAT_RATE:
            return None

        avg_surprise = float(np.mean([r["surprise_pct"] for r in history if r["beat"]]) or 0)

        # Post-earnings price moves
        post_moves = _get_post_earnings_moves(t)
        up_moves   = [m for m in post_moves if m > 0]
        down_moves = [m for m in post_moves if m < 0]
        avg_up     = round(float(np.mean(up_moves)),   1) if up_moves   else 0.0
        avg_down   = round(float(np.mean(down_moves)), 1) if down_moves else 0.0

        # Pre-earnings run behavior
        avg_pre_run  = _get_avg_pre_run(t)
        pre_momentum = _get_pre_run_momentum(t)

        # Options expected move
        expected_move = _get_expected_move(t, earnings_dt)

        # Technicals (light — we already have fast_info)
        info   = t.info
        sector = info.get("sector", "")
        name   = info.get("longName") or info.get("shortName") or ticker

        hist_daily = t.history(period="60d", interval="1d")
        rsi, macd_bullish, vol_pct, trend, atr = 50.0, True, 0.0, "Unknown", 0.0
        if not hist_daily.empty and len(hist_daily) >= 20:
            closes = hist_daily["Close"]
            volumes = hist_daily["Volume"]
            # RSI
            delta = closes.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            rsi   = round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)
            # MACD
            ema12 = closes.ewm(span=12).mean()
            ema26 = closes.ewm(span=26).mean()
            macd  = ema12 - ema26
            signal= macd.ewm(span=9).mean()
            macd_bullish = float(macd.iloc[-1]) > float(signal.iloc[-1])
            # Volume
            avg20vol = float(volumes.rolling(20).mean().iloc[-1])
            vol_pct  = round((float(volumes.iloc[-1]) - avg20vol) / avg20vol * 100, 0) if avg20vol else 0.0
            # Trend
            ma50  = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else None
            ma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None
            if ma50 and ma200:
                if price > ma50 > ma200:
                    trend = "Strong Bullish"
                elif price > ma50:
                    trend = "Bullish"
                elif price < ma50 < ma200:
                    trend = "Bearish"
                else:
                    trend = "Mixed"
            # ATR
            hi = hist_daily["High"]
            lo = hist_daily["Low"]
            tr = pd.concat([hi - lo,
                             (hi - closes.shift()).abs(),
                             (lo - closes.shift()).abs()], axis=1).max(axis=1)
            atr = round(float(tr.rolling(14).mean().iloc[-1]), 2)

        # Fundamentals
        rev_growth   = info.get("revenueGrowth")
        eps_growth   = info.get("earningsGrowth")
        gross_margin = info.get("grossMargins")
        pe           = info.get("trailingPE")
        eps_estimate = info.get("forwardEps") or info.get("epsForward")

        # Tech score proxy
        tech_score = 50
        if "Bullish" in trend: tech_score += 20
        if 40 <= rsi <= 65:    tech_score += 15
        if macd_bullish:       tech_score += 15

        # Final play score
        score = _score_play(beat_rate, len(history), avg_surprise,
                            avg_up, pre_momentum, tech_score)

        return {
            "ticker":       ticker,
            "name":         name,
            "sector":       sector,
            "score":        score,
            "price":        round(price, 2),
            "day_change":   day_chg,
            "atr":          atr,
            "earnings_date": earnings_dt,
            "days_away":    days_away,
            "timing":       timing,
            # Earnings history
            "history":      history,
            "beat_count":   beat_count,
            "beat_rate":    round(beat_rate, 3),
            "avg_surprise": round(avg_surprise, 1),
            "post_moves":   post_moves,
            "avg_up_move":  avg_up,
            "avg_down_move": avg_down,
            "up_count":     len(up_moves),
            "down_count":   len(down_moves),
            "avg_pre_run":  avg_pre_run,
            "pre_momentum": pre_momentum,
            "expected_move": expected_move,
            # Technicals
            "trend":        trend,
            "rsi":          rsi,
            "macd_bullish": macd_bullish,
            "vol_pct":      vol_pct,
            # Fundamentals
            "rev_growth":   rev_growth,
            "eps_growth":   eps_growth,
            "gross_margin": gross_margin,
            "pe":           round(pe, 1) if pe else None,
            "eps_estimate": round(eps_estimate, 2) if eps_estimate else None,
        }

    except Exception as e:
        log.debug("analyze_one skip %s: %s", ticker, e)
        return None


# ── Message formatting ─────────────────────────────────────────────────────────

def _fmt_moves(moves):
    """Format list of post-earnings moves as readable string."""
    parts = []
    for m in moves[:6]:
        arrow = "▲" if m >= 0 else "▼"
        parts.append(f"{arrow}{abs(m):.1f}%")
    return "  ".join(parts) if parts else "N/A"


def _gemini_narrative(play):
    """Generate AI narrative for this earnings play."""
    if not GEMINI_KEY:
        return ""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_KEY)
        prompt = (
            f"Write a 3-4 sentence earnings play analysis for {play['ticker']} "
            f"({play['name']}, {play['sector']} sector). "
            f"Reports earnings in {play['days_away']} days. "
            f"Beat rate: {play['beat_count']}/{len(play['history'])} quarters ({play['beat_rate']*100:.0f}%). "
            f"Avg EPS surprise when beating: +{play['avg_surprise']:.1f}%. "
            f"Avg post-earnings up move: +{play['avg_up_move']:.1f}%. "
            f"Current trend: {play['trend']}, RSI: {play['rsi']:.1f}. "
            f"Pre-earnings 5-day momentum: {play['pre_momentum']:+.1f}%. "
            f"Revenue growth: {(play['rev_growth'] or 0)*100:.0f}%, "
            f"EPS growth: {(play['eps_growth'] or 0)*100:.0f}%, "
            f"P/E: {play['pe'] or 'N/A'}x. "
            f"Explain why this is or isn't a strong earnings play, what to watch for, "
            f"and the key risk in plain English. Under 80 words, no bullet points."
        )
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=160)
        )
        return resp.text.strip()
    except Exception as e:
        log.debug("Gemini error: %s", e)
        return ""


def _format_play_message(play, risk_cfg, narrative):
    """Build the full Telegram message for one earnings play."""
    p = play
    rc = risk_cfg
    account  = rc["account_size"]
    risk_pct = rc["risk_pct_per_trade"]

    beat_pct    = round(p["beat_rate"] * 100)
    days_away   = p["days_away"]
    price       = p["price"]
    atr         = p["atr"]
    earnings_dt = p["earnings_date"]

    # ── Strategy A: Pre-earnings run (sell before announcement) ───────
    # Target = historical avg pre-run (capped at 8%), or 4% floor
    pre_target_pct = max(4.0, min(8.0, p["avg_pre_run"] * 0.8)) if p["avg_pre_run"] > 1 else 4.0
    a_entry  = price
    a_target = round(price * (1 + pre_target_pct / 100), 2)
    a_stop   = round(price - atr * 1.5, 2)
    a_risk   = round(a_entry - a_stop, 2)
    a_reward = round(a_target - a_entry, 2)
    a_rr     = round(a_reward / a_risk, 1) if a_risk > 0 else 0.0

    # ── Strategy B: Hold through earnings ─────────────────────────────
    avg_up   = p["avg_up_move"] if p["avg_up_move"] > 0 else 8.0
    b_entry  = price
    b_target = round(price * (1 + avg_up / 100), 2)
    b_stop   = a_stop    # same stop — exit if gap down at open
    b_risk   = round(b_entry - b_stop, 2)
    b_reward = round(b_target - b_entry, 2)
    b_rr     = round(b_reward / b_risk, 1) if b_risk > 0 else 0.0

    # ── Position size (based on Strategy A risk) ───────────────────────
    risk_dollars = account * risk_pct
    shares       = int(risk_dollars / a_risk) if a_risk > 0 else 0
    pos_value    = round(shares * price, 0)
    pos_pct      = round(pos_value / account * 100, 1)
    actual_risk  = round(shares * a_risk, 2)

    # ── Beat/miss quality label ────────────────────────────────────────
    beat_emoji = "🟢" if p["beat_rate"] >= 0.75 else "🟡" if p["beat_rate"] >= 0.60 else "🟠"

    # ── Timing label ───────────────────────────────────────────────────
    timing_map = {
        "BMO": "Before Market Open  →  gap visible at open",
        "AMC": "After Market Close  →  react next morning",
        "Unknown": "Time TBD (check before market open)",
    }
    timing_label = timing_map.get(p["timing"], "TBD")

    # ── Pre-run context ────────────────────────────────────────────────
    if p["pre_momentum"] > 10:
        prerun_note = f"⚠️ Already up {p['pre_momentum']:.1f}% this week — partially priced in"
    elif p["pre_momentum"] > 2:
        prerun_note = f"✅ Up {p['pre_momentum']:.1f}% this week — smart money positioning"
    elif p["pre_momentum"] < -3:
        prerun_note = f"📉 Down {abs(p['pre_momentum']):.1f}% this week — weak going in"
    else:
        prerun_note = "➡️ Flat this week — clean entry, no pre-run premium"

    # ── Fundamentals line ──────────────────────────────────────────────
    def pct(v):
        return f"{v*100:+.0f}%" if v is not None else "N/A"

    fund_line = (
        f"Rev {pct(p['rev_growth'])} | EPS {pct(p['eps_growth'])} | "
        f"GM {pct(p['gross_margin'])} | P/E {p['pe']}x"
        if p["pe"] else
        f"Rev {pct(p['rev_growth'])} | EPS {pct(p['eps_growth'])} | GM {pct(p['gross_margin'])}"
    )

    # ── Warning block ──────────────────────────────────────────────────
    warnings = []
    if p["beat_rate"] < 0.65:
        warnings.append(f"Modest beat rate ({beat_pct}%) — Strategy A is safer")
    if p["pre_momentum"] > 10:
        warnings.append("Stock already running — upside may be priced in")
    if p["pe"] and p["pe"] > 40:
        warnings.append(f"High P/E ({p['pe']}x) — any miss will be punished hard")
    if p["avg_down_move"] < -15:
        warnings.append(f"Misses are brutal: avg -{abs(p['avg_down_move']):.1f}% when they disappoint")
    if days_away == 1:
        warnings.append("Reports TOMORROW — only Strategy A has time to work cleanly")

    # ── Assemble message ───────────────────────────────────────────────
    lines = [
        f"🎯 EARNINGS PLAY — {p['ticker']} — {p['name']}",
        f"Score: {p['score']}/100  |  {p['sector'] or 'Unknown Sector'}",
        "",
        "— EARNINGS DATE —",
        f"📅  {earnings_dt.strftime('%A, %b %d, %Y')}  "
        f"({'tomorrow' if days_away == 1 else f'in {days_away} days'})",
        f"⏰  {timing_label}",
        "",
        f"— BEAT TRACK RECORD  (last {len(p['history'])} quarters) —",
        f"{beat_emoji}  Beat rate:   {p['beat_count']}/{len(p['history'])} = {beat_pct}%",
        f"📊  Avg EPS beat:  +{p['avg_surprise']:.1f}% above estimate",
        f"📈  Post-earnings: {_fmt_moves(p['post_moves'])}",
        f"    Avg UP:   +{p['avg_up_move']:.1f}%   ({p['up_count']} times)",
        f"    Avg DOWN:  {p['avg_down_move']:.1f}%   ({p['down_count']} times)",
    ]

    if p["expected_move"]:
        lines.append(f"⚙️  Options imply: ±{p['expected_move']:.1f}% move around earnings")
    if p["avg_pre_run"] != 0:
        lines.append(f"📉  Avg pre-run (5 days before): {p['avg_pre_run']:+.1f}%")

    lines += [
        "",
        "— CURRENT SETUP —",
        f"Price: ${price}  ({'▲' if p['day_change'] >= 0 else '▼'}{abs(p['day_change']):.2f}%)",
        f"{prerun_note}",
        f"Trend: {p['trend']}  |  RSI: {p['rsi']:.1f}  |  MACD: {'Bullish ▲' if p['macd_bullish'] else 'Bearish ▼'}",
        f"Volume: {'+' if p['vol_pct'] >= 0 else ''}{p['vol_pct']:.0f}% vs 20-day avg",
        "",
        "— FUNDAMENTALS —",
        fund_line,
    ]
    if p["eps_estimate"]:
        lines.append(f"Analyst EPS estimate this quarter: ${p['eps_estimate']:.2f}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "STRATEGY A — Pre-Earnings Run  (LOWER RISK)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Entry:   Buy at market open (~${price})",
        f"Target:  ${a_target}  (+{pre_target_pct:.1f}% typical pre-run)",
        f"Stop:    ${a_stop}  (1.5× ATR below entry)",
        f"R:R:     {a_rr}:1  |  Risk per share: ${a_risk}",
        "",
        "What you're doing:",
        f"Stocks with strong beat history often drift up 3-8%",
        f"in the days before earnings as funds position early.",
        f"You capture that move with ZERO binary risk.",
        "",
        "EXIT RULES (Strategy A):",
        f"✅  Sell 100% at close the day BEFORE earnings.",
        f"    Do NOT hold through the announcement.",
        f"✅  If target ${a_target} hits early — take profit, done.",
        f"🛑  If price drops to ${a_stop} — exit immediately.",
        f"⏰  If flat after {max(2, days_away - 1)} days — exit, cut losses.",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "STRATEGY B — Hold Through Earnings  (HIGHER RISK)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Entry:   Buy at market open (~${price})",
        f"Target:  ${b_target}  (+{avg_up:.1f}% avg historical beat move)",
        f"Stop:    ${b_stop}  (exit if gap down at open)",
        f"R:R:     {b_rr}:1  |  Risk per share: ${b_risk}",
        "",
        "What you're doing:",
        f"Holding through the announcement to capture the",
        f"full post-earnings gap. Pays {b_rr}:1 if right.",
        f"This stock beats {beat_pct}% of the time — odds are in your favour.",
        "",
        "EXIT RULES (Strategy B):",
        f"📈  If gaps UP at open:",
        f"    → Sell 50% immediately at open",
        f"    → Move stop to breakeven on remaining 50%",
        f"    → Sell remaining at ${b_target} or end of Day 1",
        f"📉  If gaps DOWN at open:",
        f"    → Exit 100% in the first 15 minutes — no holding",
        f"    → A down gap means something is wrong — don't average down",
        f"➡️  If gap is small (< 2%) in either direction:",
        f"    → Wait for direction to develop by 10:30 AM",
        f"    → Exit by end of Day 2 regardless",
        "",
        "— POSITION SIZE —",
        f"(${account:,} account, {risk_pct*100:.0f}% max risk per trade)",
        f"Shares: {shares}  |  Value: ${pos_value:,.0f} ({pos_pct}%)",
        f"Max risk: ${actual_risk}  ({round(actual_risk/account*100, 2)}% of account)",
    ]

    if warnings:
        lines += ["", "⚠️  WARNINGS:"]
        for w in warnings:
            lines.append(f"   • {w}")

    if narrative:
        lines += ["", "💬  AI ANALYSIS:", narrative]

    lines += [
        "",
        "⚠️  Technical + earnings history analysis only.",
        "Not financial advice. Earnings are binary events.",
    ]

    return "\n".join(lines)


# ── Summary message ────────────────────────────────────────────────────────────

def _format_summary(plays, today):
    """Short summary listing all plays before the detailed messages."""
    lines = [
        f"📅  EARNINGS PLAYS — {today.strftime('%A, %b %d, %Y')}",
        f"Found {len(plays)} quality earnings play{'s' if len(plays) != 1 else ''} "
        f"in the next {DAYS_AHEAD} trading days.",
        "",
        "— UPCOMING PLAYS —",
    ]
    for i, p in enumerate(plays, 1):
        days = p["days_away"]
        label = "tomorrow" if days == 1 else f"in {days} days"
        beat_pct = round(p["beat_rate"] * 100)
        lines.append(
            f"#{i}  {p['ticker']} — {p['name'][:30]}"
            f"\n     Reports {label} ({p['earnings_date'].strftime('%a %b %d')})"
            f" | Beat rate {beat_pct}% | Score {p['score']}"
        )
    lines += [
        "",
        "Detailed analysis for each play follows below ↓",
        "Each message includes two strategies + full exit rules.",
    ]
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from telegram_utils import send_messages

    today  = date.today()
    cutoff = today + timedelta(days=DAYS_AHEAD)

    log.info("Earnings scanner starting — looking for reports between %s and %s",
             today, cutoff)

    tickers  = _get_universe()
    log.info("Universe: %d tickers", len(tickers))

    plays = []
    done  = 0
    total = len(tickers)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_analyze_one, t, today, cutoff): t for t in tickers}
        for fut in as_completed(futures):
            done += 1
            try:
                result = fut.result()
                if result:
                    plays.append(result)
                    log.info("✓ EARNINGS PLAY: %s  score=%s  beats=%s/%s  days=%s",
                             result["ticker"], result["score"],
                             result["beat_count"], len(result["history"]),
                             result["days_away"])
            except Exception as e:
                log.debug("future error %s: %s", futures[fut], e)

            if done % 100 == 0:
                log.info("Progress: %d/%d scanned, %d plays found", done, total, len(plays))

    # Sort by score descending, then days_away ascending (sooner first)
    plays.sort(key=lambda x: (-x["score"], x["days_away"]))

    log.info("Scan complete. %d plays found out of %d tickers.", len(plays), total)

    if not plays:
        msg = (
            f"📅 Earnings Scanner — {today.strftime('%A, %b %d, %Y')}\n\n"
            f"No qualifying earnings plays found for the next {DAYS_AHEAD} trading days.\n"
            f"Criteria: ≥{MIN_BEAT_RATE*100:.0f}% beat rate, ≥{MIN_QUARTERS} quarters history, "
            f"market cap ≥$2B, avg volume ≥500K."
        )
        if TG_TOKEN and TG_CHAT_ID:
            send_messages(TG_TOKEN, TG_CHAT_ID, msg)
        print(msg)
        return

    risk_cfg = CONFIG["risk"]

    # Send summary first
    if TG_TOKEN and TG_CHAT_ID:
        send_messages(TG_TOKEN, TG_CHAT_ID, _format_summary(plays, today))

    # Send detailed message for each play
    for play in plays:
        log.info("Generating detailed message for %s…", play["ticker"])
        narrative = _gemini_narrative(play)
        msg = _format_play_message(play, risk_cfg, narrative)

        print("\n" + "="*60)
        print(msg)

        if TG_TOKEN and TG_CHAT_ID:
            send_messages(TG_TOKEN, TG_CHAT_ID, msg)


if __name__ == "__main__":
    main()
