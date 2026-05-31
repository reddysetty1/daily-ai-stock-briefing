"""
formatter.py — Telegram message assembly for the daily scanner.
Produces:
  1. Ranked summary (one message, always first)
  2. Per-stock detail (one message per pick)
  3. EOD performance summary

Keeps all messages under 4096 chars.
"""

from datetime import datetime


def _fmt_pct(v) -> str:
    if v is None: return "n/a"
    return f"{v*100:.0f}%"


def _fmt_price(v) -> str:
    if v is None: return "n/a"
    return f"${v:.2f}"


def _rr_emoji(rr: float) -> str:
    if rr >= 3.0: return "🟢"
    if rr >= 2.0: return "🟡"
    return "🔴"


def format_ranked_summary(picks: list, market_data: dict, total_scanned: int,
                           date_str: str = None) -> str:
    """
    One-message ranked summary of all picks.
    picks: list of result dicts (sorted by score, best first).
    """
    date_str = date_str or datetime.now().strftime("%A, %B %d %Y")
    spy_chg  = market_data.get("SPY", {}).get("day_change_pct", 0)
    qqq_chg  = market_data.get("QQQ", {}).get("day_change_pct", 0)
    iwm_chg  = market_data.get("IWM", {}).get("day_change_pct", 0)
    spy_pr   = market_data.get("SPY", {}).get("price", 0)
    qqq_pr   = market_data.get("QQQ", {}).get("price", 0)

    mood = "Bullish" if spy_chg > 0.3 else "Bearish" if spy_chg < -0.3 else "Neutral"

    lines = [
        f"📊 Daily Scan — {date_str}",
        f"⏰ Pre-Market | Scanned {total_scanned} stocks\n",
        "— MARKET CONTEXT —",
        f"SPY: ${spy_pr:.2f} ({'+' if spy_chg >= 0 else ''}{spy_chg:.2f}%) | "
        f"QQQ: ${qqq_pr:.2f} ({'+' if qqq_chg >= 0 else ''}{qqq_chg:.2f}%) | "
        f"IWM: ({'+' if iwm_chg >= 0 else ''}{iwm_chg:.2f}%)",
        f"Broad bias: {mood}\n",
        "— TOP PICKS TODAY —",
    ]

    if not picks:
        lines.append("No stocks met the minimum score threshold today.")
        lines.append("Market conditions may be unfavourable for fresh entries.")
    else:
        for i, p in enumerate(picks, 1):
            t      = p["ticker"]
            score  = p["score"]
            setup  = p["setup"]
            ep     = p["entry"]["entry_plan"]
            ex     = p["entry"]["exit_plan"]
            rr1    = ex["rr1"]
            rr_e   = _rr_emoji(rr1)
            el     = ep.get("entry_low")
            eh     = ep.get("entry_high")
            entry_str = f"${el}-${eh}" if el and eh else "Watch"

            lines.append(
                f"#{i} {t} {score:.0f}pts | {setup.title()} | "
                f"Entry {entry_str} | Stop ${ex['stop']} | "
                f"T1 ${ex['t1']} | R:R {rr1} {rr_e}"
            )

    lines += [
        "",
        "— RANKED LABELS —",
    ]

    if picks:
        best_bo = next((p for p in picks if p["setup"] == "breakout"),  None)
        best_pb = next((p for p in picks if p["setup"] == "pullback"),  None)
        best_rev = next((p for p in picks if p["setup"] == "reversal"), None)
        wait_picks = [p for p in picks if p["setup"] == "wait"]

        if best_bo:  lines.append(f"🚀 Best Breakout:  {best_bo['ticker']}")
        if best_pb:  lines.append(f"📉 Best Pullback:  {best_pb['ticker']}")
        if best_rev: lines.append(f"↩️ Best Reversal:  {best_rev['ticker']}")
        if wait_picks:
            tickers = ", ".join(p["ticker"] for p in wait_picks)
            lines.append(f"👁️ Watchlist Only:  {tickers}")

    lines.append("\n⚠️ For educational purposes only. Not financial advice.")
    return "\n".join(lines)


def format_stock_detail(p: dict, account_size: float) -> str:
    """Full per-stock detail message."""
    t      = p["ticker"]
    name   = p.get("fund", {}).get("name", t)
    score  = p["score"]
    setup  = p["setup"]
    tech   = p["tech"]
    fund   = p.get("fund", {})
    ep     = p["entry"]["entry_plan"]
    ex     = p["entry"]["exit_plan"]
    pos    = p["entry"]["position"]
    bd     = p["breakdown"]
    qual   = p["quality"]
    flags  = p["flags"]
    narr   = p.get("narrative", "")

    # Score breakdown bar
    def bar(v):
        filled = round(v / 10)
        return "█" * filled + "░" * (10 - filled)

    cp  = tech["current_price"]
    chg = tech["day_change_pct"]
    arrow = "▲" if chg >= 0 else "▼"

    lines = [
        f"🎯 {t} — {name}",
        f"Score: {score:.0f}/100 | Setup: {setup.title()} | Quality: {qual.upper()}\n",

        "— PRICE —",
        f"Now: ${cp} {arrow}{abs(chg):.2f}% | ATR(14): ${tech['atr14']}",
        f"52wk: ${tech['week52_low']} — ${tech['week52_high']}",
        f"Attractiveness: {tech['attractiveness'].title()}\n",

        "— TECHNICALS —",
        f"Trend: {tech['trend']}",
        f"Daily RSI: {tech['rsi']} | Weekly RSI: {tech.get('weekly_rsi','n/a')}",
        f"MACD: {'Bullish' if tech['macd'] > tech['macd_signal'] else 'Bearish'} "
        f"(hist {tech['macd_hist']:+.3f})",
        f"Volume: {tech['vol_pct']:+.0f}% vs avg | Trend: {tech['vol_trend']}\n",
    ]

    # Fundamentals (only if data available)
    fund_parts = []
    if fund.get("revenue_growth"):  fund_parts.append(f"Rev +{fund['revenue_growth']*100:.0f}%")
    if fund.get("earnings_growth"): fund_parts.append(f"EPS +{fund['earnings_growth']*100:.0f}%")
    if fund.get("gross_margin"):    fund_parts.append(f"GM {fund['gross_margin']*100:.0f}%")
    if fund.get("trailing_pe"):     fund_parts.append(f"P/E {fund['trailing_pe']:.0f}x")
    if fund.get("debt_to_equity"):  fund_parts.append(f"D/E {fund['debt_to_equity']:.0f}")
    if fund_parts:
        lines += ["— FUNDAMENTALS —", " | ".join(fund_parts), ""]

    lines += [
        "— TRADE PLAN —",
        f"Setup Style:  {ep['style']}",
    ]

    if ep.get("entry_low"):
        lines.append(f"Entry Zone:   ${ep['entry_low']} – ${ep['entry_high']}")
        lines.append(f"Confirm:      {ep['confirmation']}")
        lines.append(f"Stop-Loss:    ${ex['stop']} (distance: ${ex['stop_distance']})")
        lines.append(f"Target 1:     ${ex['t1']} | R:R {ex['rr1']}:1 {_rr_emoji(ex['rr1'])}")
        lines.append(f"Target 2:     ${ex['t2']} | R:R {ex['rr2']}:1")
        lines.append(f"Trailing:     {ex['trailing_rule']}")
        lines.append(f"Time Exit:    If no move by Day {ex['time_exit_days']}")
    else:
        lines.append("No clean entry — monitor for setup development")

    lines += [""]

    if pos and pos["shares"] > 0:
        lines += [
            f"— POSITION SIZE (${account_size:,.0f} acct, 1% risk) —",
            f"Shares: {pos['shares']} | Value: ${pos['position_value']:,.0f} ({pos['position_pct']}%)",
            f"Risk: ${pos['risk_dollars']} ({pos['risk_pct']}% of account)",
            "",
        ]

    lines += [
        "— SCORE BREAKDOWN —",
        f"Trend {bd['trend']:.0f} | Mom {bd['momentum']:.0f} | Vol {bd['volume']:.0f} | "
        f"Sector {bd['sector']:.0f} | Fund {bd['fundamental']:.0f} | Risk -{bd['risk_penalty']:.0f}",
        "",
    ]

    if flags:
        lines.append("⚠️ FLAGS:")
        for f in flags:
            lines.append(f"  • {f}")
        lines.append("")

    lines.append(f"❌ Invalidation: {ep.get('invalidation', 'N/A')}")

    if narr:
        lines += ["", "💬 ANALYSIS:", narr]

    lines.append("\n⚠️ Technical + fundamental analysis only. Not financial advice.")
    return "\n".join(lines)


def format_eod_summary(picks: list, date_str: str = None) -> str:
    """EOD performance check against morning entry zones."""
    date_str = date_str or datetime.now().strftime("%A, %B %d %Y")
    lines = [
        f"📊 EOD Summary — {date_str}\n",
        "— TODAY'S PICKS PERFORMANCE —",
    ]

    if not picks:
        lines.append("No picks were generated this morning.")
        return "\n".join(lines)

    performers = []
    for p in picks:
        t    = p["ticker"]
        ep   = p["entry"]["entry_plan"]
        tech = p["tech"]
        cp   = tech["current_price"]  # now = EOD price (re-fetched)
        el   = ep.get("entry_low")
        eh   = ep.get("entry_high")
        ex   = p["entry"]["exit_plan"]
        stop = ex["stop"]
        t1   = ex["t1"]

        if el and eh:
            if cp >= el and cp <= eh:
                status = "✅ In entry zone"
            elif cp > eh:
                # Did it run up through?
                chg_from_entry = round((cp - eh) / eh * 100, 1)
                status = f"📈 Ran +{chg_from_entry}% above entry zone"
            elif cp < el and cp > stop:
                status = f"⚠️ Below entry zone (stop not hit)"
            elif cp <= stop:
                status = f"🛑 Stop-loss zone hit"
            else:
                status = "—"
        else:
            status = "👁️ Watch only"

        performers.append(f"{t}: EOD ${cp} | Entry ${el}-${eh} | {status}")

    lines += performers
    lines += [
        "",
        "⚠️ Past performance does not guarantee future results.",
    ]
    return "\n".join(lines)
