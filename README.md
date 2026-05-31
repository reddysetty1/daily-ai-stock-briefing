# 📊 Daily AI Stock Briefing & Scanner Bot

A fully automated stock analysis system with a **local web dashboard** and daily Telegram delivery.
Powered by **Google Gemini AI** + **live market data** via yfinance, scheduled free on **GitHub Actions**.

No server. No cost. Wake up, open your dashboard, and get institutional-grade trade ideas.

---

## What it does

| Feature | How | When |
|---------|-----|------|
| 🖥️ **Web Dashboard** | `python app.py` → opens browser | Any time (local) |
| 📈 **Daily stock scanner** — top picks with trade plans | GitHub Actions | 5:45 AM PST |
| 🌅 **Pre-market snapshot** | GitHub Actions | 6:30 AM PST |
| 🔍 **Deep market analysis** | GitHub Actions | 8:00 AM PST |
| 📉 **EOD performance check** vs morning entry zones | GitHub Actions | 3:15 PM PST |
| 🤖 **Telegram bot** — send any ticker, get full analysis | GitHub Actions (poll) | Every 5 min |
| 🎯 **Manual prediction** via GitHub Actions UI | workflow_dispatch | On demand |

---

## 🖥️ Web Dashboard — open every morning

The dashboard is a local Flask web app. **One double-click to launch it.**

### First-time setup (once only)

```bash
git clone https://github.com/YOUR_USERNAME/daily-ai-stock-briefing
cd daily-ai-stock-briefing
pip install -r requirements.txt
cp .env.example .env        # fill in your API keys
```

### Daily use — Windows

**Double-click `launch.bat`** in the project folder. It opens the dashboard at `http://localhost:5000` automatically.

Or from terminal:
```bash
python app.py
```

### Daily use — Mac / Linux

**Double-click `launch.command`** in the project folder, or:
```bash
python app.py
```

> The app auto-opens your browser. Just leave the terminal window running in the background while you use the dashboard.

---

## Dashboard features

### 🔍 Analyzer tab
- Type any ticker — autocomplete covers **10,000+ stocks** across NYSE, NASDAQ, S&P 500, Dow Jones
- Full analysis in ~5 seconds: price, technicals, trade plan (entry zone / stop / T1 / T2), position sizing, score breakdown, AI narrative
- Toggle **Send to Telegram** to push results to your phone simultaneously

### 📈 Daily Scanner tab
- Scans **all 10,000+ stocks** in parallel (12 workers) — completes in ~15–20 min
- Live progress — every ticker appears as it's scored, colour-coded green / yellow / red
- Top picks shown as cards with entry zone, stop, targets, R:R ratio
- Click any pick → drills into the Analyzer tab with full detail

---

## Universe coverage

| Source | Exchange | Stocks |
|--------|----------|--------|
| SEC EDGAR | NYSE | ~3,100 |
| SEC EDGAR | NASDAQ | ~4,300 |
| Wikipedia | S&P 500 | 503 |
| Wikipedia | NASDAQ 100 | 101 |
| Wikipedia | Dow Jones | 30 |
| **Total unique** | | **~10,200** |

The universe auto-refreshes from its sources every 7 days. No manual maintenance needed.

---

## Sample Telegram messages

### Morning Briefing (6:30 AM)
```
📊 Tuesday, May 20, 2026 | Pre-Market Briefing

— MARKET SNAPSHOT —
📈 SPY: $540.12 up 0.30% | QQQ: $460.88 down 0.10%
🌍 Mood: Cautious — Investors await key inflation data.

— TOP ANALYST MOVES —
🟢 BUY: Apple ($AAPL) | Now: $197.84 | Target: $220 by Dec 2026
🔴 SELL: NVIDIA ($NVDA) | Now: $222.32 | Downside: $195 by Aug 2026
```

### Daily Scanner (5:45 AM)
```
📊 Daily Scan — Tuesday, May 20, 2026
⏰ Pre-Market | Scanned 10196 stocks

— TOP PICKS TODAY —
#1 MSFT 74pts | Pullback | Entry $415-$420 | Stop $408 | T1 $432 | R:R 2.8 🟡
#2 AMZN 68pts | Breakout | Entry $192-$195 | Stop $187 | T1 $205 | R:R 2.4 🟡
```

### Per-Stock Detail
```
🎯 MSFT — Microsoft Corporation
Score: 74/100 | Setup: Pullback | Quality: GOOD

— TRADE PLAN —
Entry Zone:   $415.00 – $420.00
Stop-Loss:    $408.00 (distance: $7.00)
Target 1:     $432.00 | R:R 2.4:1 🟡
Target 2:     $448.00 | R:R 4.6:1

— POSITION SIZE ($10,000 acct, 1% risk) —
Shares: 14 | Value: $5,852 | Risk: $98
```

### EOD Summary (3:15 PM)
```
📊 EOD Summary — Tuesday, May 20, 2026

MSFT: EOD $424.50 | Entry $415-$420 | 📈 Ran +1.1% above entry zone
AMZN: EOD $193.20 | Entry $192-$195 | ✅ In entry zone
```

---

## Tech Stack

| Tool | Purpose |
|------|---------|
| [Flask](https://flask.palletsprojects.com) | Local web dashboard |
| [Google Gemini API](https://aistudio.google.com) | AI narratives and briefing |
| [yfinance](https://github.com/ranaroussi/yfinance) | Real-time + historical market data |
| [SEC EDGAR API](https://www.sec.gov/cgi-bin/browse-edgar) | Full NYSE + NASDAQ universe |
| [Telegram Bot API](https://core.telegram.org/bots/api) | Message delivery |
| [GitHub Actions](https://github.com/features/actions) | Free cloud scheduler |
| Python 3.11+ | Runtime |

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  LOCAL  — python app.py → http://localhost:5000  │
│                                                  │
│  Analyzer tab   → analyze any ticker instantly   │
│  Scanner tab    → full 10K-stock scan on demand  │
│  Both tabs      → optionally send to Telegram    │
└─────────────────────────────────────────────────┘

GitHub Actions (cron — automatic, no local machine needed)
        │
        ├─ 5:45 AM PST ─→ daily_scan.py
        │                     ├─ Score 10K+ stocks in parallel (10 workers)
        │                     ├─ Select top picks above score threshold
        │                     ├─ Generate entry/exit/position plans
        │                     ├─ Gemini narrative per pick
        │                     └─ Send ranked summary + detail to Telegram
        │
        ├─ 6:30 AM PST ─→ briefing.py (pre-market snapshot) → Telegram
        ├─ 8:00 AM PST ─→ briefing.py (deep analysis)       → Telegram
        │
        ├─ 3:15 PM PST ─→ eod_summary.py
        │                     └─ Re-fetch EOD prices, report vs entry zones
        │
        └─ Every 5 min ─→ bot.py
                              └─ Telegram: send ticker → get full analysis reply
```

---

## Scoring Model

Each stock is scored 0–100 using a weighted multi-factor model:

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| Trend | 22% | Price vs MA200, MA50, golden cross, EMA9 |
| Momentum | 20% | RSI range, MACD direction, weekly MACD |
| Fundamentals | 20% | Revenue growth, EPS, gross margin, FCF, P/E, balance sheet |
| Volume | 13% | Volume vs 20-day avg + volume trend |
| Sector | 10% | Stock vs its sector ETF (XLF, QQQ, XLV…) |
| Risk Penalty | 15% | Overbought RSI, extended price, earnings proximity, high beta |

---

## Setup Detection

| Setup | Conditions |
|-------|-----------|
| **Pullback** | Uptrend + price at support/EMA + declining volume + RSI 33–58 |
| **Breakout** | Near resistance + volume > 115% avg + MACD bullish + RSI 45–72 |
| **Reversal** | RSI < 35 + at major support or weekly RSI < 40 |
| **Wait** | None of the above — watchlist only |

---

## Fork and run it yourself

### 1. Get your API keys

| Key | Where to get it |
|-----|----------------|
| `GEMINI_API_KEY` | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) — free |
| `TELEGRAM_BOT_TOKEN` | Message `@BotFather` on Telegram → `/newbot` |
| `TELEGRAM_CHAT_ID` | Message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` |

### 2. Fork this repo

Click the **Fork** button at the top of this page.

### 3. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|-------------|-------|
| `GEMINI_API_KEY` | Your Gemini API key |
| `GEMINI_MODEL` | e.g. `gemini-2.0-flash` or `gemini-3.1-flash-lite` |
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

### 4. Enable GitHub Actions

Go to the **Actions** tab → **"I understand my workflows, go ahead and enable them"**.

### 5. Launch the dashboard locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python app.py          # opens http://localhost:5000
```

---

## Customize

| What | Where |
|------|-------|
| Scoring weights | `config.json` → `weights` |
| Account size & risk per trade | `config.json` → `risk` |
| Minimum score threshold | `config.json` → `selection.min_score` |
| Schedule times | `.github/workflows/*.yml` cron expressions |
| Gemini model | `GEMINI_MODEL` secret or `.env` |
| Universe refresh interval | `universe.py` → `CACHE_TTL` (default: 7 days) |

---

## Project structure

```
├── app.py               # Local web dashboard (Flask)
├── briefing.py          # Pre-market & deep-analysis Telegram briefings
├── predict.py           # Single-stock intraday + weekly prediction
├── bot.py               # Telegram polling bot
├── daily_scan.py        # Pre-market stock selection & trade planning
├── eod_summary.py       # EOD performance check
│
├── universe.py          # Dynamic universe loader (SEC + Wikipedia, ~10K stocks)
├── tech_analysis.py     # RSI, MACD, ATR, Bollinger, support/resistance
├── fundamentals.py      # yfinance fundamentals fetch + scoring
├── scoring.py           # Multi-factor stock scoring engine
├── signals.py           # Setup detection + entry/exit plan generation
├── risk.py              # Position sizing + quality assessment
├── formatter.py         # Telegram message assembly
├── telegram_utils.py    # Message splitting (4096 char limit)
│
├── config.json          # Weights, risk params, selection thresholds
├── requirements.txt
├── .env.example
├── launch.bat           # Windows one-click launcher
├── launch.command       # Mac/Linux one-click launcher
│
└── .github/workflows/
    ├── premarket_briefing.yml   # 6:30 AM PST
    ├── market_analysis.yml      # 8:00 AM PST
    ├── daily_scan.yml           # 5:45 AM PST
    ├── eod_summary.yml          # 3:15 PM PST
    ├── telegram_bot.yml         # Every 5 min
    └── predict_stock.yml        # Manual via UI
```

---

## License

MIT — free to use, fork, and modify.
