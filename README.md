# 📊 Daily AI Stock Briefing & Scanner Bot

A fully automated stock analysis system delivered to Telegram every weekday.
Powered by **Google Gemini AI** + **live market data** via yfinance, scheduled free on **GitHub Actions**.

No server. No cost. Wake up to your briefing — and get trade ideas with entry, stop, and target prices.

---

## What it does

| Feature | Time (PST) | Script |
|---------|-----------|--------|
| 🌅 Pre-market snapshot | 6:30 AM | `briefing.py` |
| 🔍 Deep market analysis | 8:00 AM | `briefing.py` |
| 📈 Daily stock scanner — top picks with trade plans | 5:45 AM | `daily_scan.py` |
| 📉 EOD performance check vs morning entry zones | 3:15 PM | `eod_summary.py` |
| 🤖 On-demand ticker analysis via Telegram bot | Any time | `bot.py` |
| 🎯 Manual single-stock prediction (GitHub Actions UI) | On demand | `predict.py` |

---

## Sample messages

### Morning Briefing (6:30 AM)
```
📊 Tuesday, May 19, 2026 | Pre-Market Briefing

— MARKET SNAPSHOT —
📈 SPY: $540.12 up 0.30% | QQQ: $460.88 down 0.10%
🌍 Mood: Cautious — Investors await key inflation data.

— TOP ANALYST MOVES —
🟢 BUY: Apple ($AAPL) | Now: $197.84 | Target: $220 by Dec 2026
🔴 SELL: NVIDIA ($NVDA) | Now: $222.32 | Downside: $195 by Aug 2026

— HOT NEWS —
🇺🇸 White House signals new AI infrastructure investment order.
```

### Daily Scanner (5:45 AM)
```
📊 Daily Scan — Tuesday, May 20, 2026
⏰ Pre-Market | Scanned 40 stocks

— MARKET CONTEXT —
SPY: $540.12 (+0.30%) | QQQ: $460.88 (+0.40%) | IWM: (+0.10%)
Broad bias: Neutral

— TOP PICKS TODAY —
#1 MSFT 74pts | Pullback | Entry $415-$420 | Stop $408 | T1 $432 | R:R 2.8 🟡
#2 AMZN 68pts | Breakout | Entry $192-$195 | Stop $187 | T1 $205 | R:R 2.4 🟡

— RANKED LABELS —
🚀 Best Breakout:  AMZN
📉 Best Pullback:  MSFT
```

### Per-Stock Detail (one message per pick)
```
🎯 MSFT — Microsoft Corporation
Score: 74/100 | Setup: Pullback | Quality: GOOD

— TRADE PLAN —
Setup Style:  Pullback to support
Entry Zone:   $415.00 – $420.00
Confirm:      Bullish candle or RSI turning up from near $417
Stop-Loss:    $408.00 (distance: $7.00)
Target 1:     $432.00 | R:R 2.4:1 🟡
Target 2:     $448.00 | R:R 4.6:1
Trailing:     Move stop to entry once price hits $423.50
Time Exit:    If no move by Day 3

— POSITION SIZE ($10,000 acct, 1% risk) —
Shares: 14 | Value: $5,852 (58.5%)
Risk: $98 (0.98% of account)
```

### EOD Summary (3:15 PM)
```
📊 EOD Summary — Tuesday, May 20, 2026

— TODAY'S PICKS PERFORMANCE —
MSFT: EOD $424.50 | Entry $415-$420 | 📈 Ran +1.1% above entry zone
AMZN: EOD $193.20 | Entry $192-$195 | ✅ In entry zone
```

---

## Tech Stack

| Tool | Purpose |
|------|---------|
| [Google Gemini API](https://aistudio.google.com) | AI narratives and briefing generation |
| [yfinance](https://github.com/ranaroussi/yfinance) | Free real-time + historical stock data |
| [Telegram Bot API](https://core.telegram.org/bots/api) | Message delivery & interactive bot |
| [GitHub Actions](https://github.com/features/actions) | Free cloud scheduler (no server needed) |
| Python 3.11+ | Runtime |

---

## Architecture

```
GitHub Actions (cron)
        │
        ├─ 5:45 AM PST ─→ daily_scan.py
        │                     ├─ Fetch tech indicators (RSI, MACD, ATR, S/R) for 40 stocks
        │                     ├─ Fetch fundamentals (revenue, EPS, margins, P/E)
        │                     ├─ Score each stock (trend + momentum + volume + sector + fundamentals − risk)
        │                     ├─ Detect setup (breakout / pullback / reversal / wait)
        │                     ├─ Generate entry zone, stop-loss, T1, T2, position size
        │                     ├─ Gemini narrative per pick
        │                     └─ Send ranked summary + per-stock detail to Telegram
        │
        ├─ 6:30 AM PST ─→ briefing.py (BRIEFING_MODE=premarket) → Telegram
        ├─ 8:00 AM PST ─→ briefing.py (BRIEFING_MODE=analysis)  → Telegram
        │
        ├─ 3:15 PM PST ─→ eod_summary.py
        │                     ├─ Re-fetch EOD prices for morning picks
        │                     └─ Report entry zone hit / ran above / stop hit / watch
        │
        └─ Every 5 min ─→ bot.py
                              ├─ Poll Telegram getUpdates
                              └─ Reply to any ticker message with full predict.py analysis

Manual triggers (GitHub Actions UI)
        └─ predict_stock.yml → input ticker → predict.py → Telegram
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
| Sector | 10% | Stock performance relative to its sector ETF |
| Risk Penalty | 15% | Overbought RSI, extended price, earnings proximity, high beta |

Top 3–6 stocks above a minimum score of 50 are selected as picks.

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

Go to your forked repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|-------------|-------|
| `GEMINI_API_KEY` | Your Gemini API key |
| `GEMINI_MODEL` | e.g. `gemini-2.0-flash` or `gemini-3.1-flash-lite` |
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

### 4. Enable GitHub Actions

Go to the **Actions** tab → click **"I understand my workflows, go ahead and enable them"**.

That's it. All workflows run automatically on weekdays.

---

## Local development

```bash
git clone https://github.com/YOUR_USERNAME/daily-ai-stock-briefing
cd daily-ai-stock-briefing

pip install -r requirements.txt

cp .env.example .env
# Fill in your keys in .env

# Test the daily scanner (sends to Telegram)
python daily_scan.py

# Test the EOD summary
python eod_summary.py

# Test single-stock prediction
set STOCK_TICKER=AAPL && python predict.py   # Windows
STOCK_TICKER=AAPL python predict.py          # Mac/Linux

# Test morning briefing
set BRIEFING_MODE=premarket && python briefing.py
```

---

## Customize

| What | Where |
|------|-------|
| Stock universe (which 40 stocks to scan) | `config.json` → `universe` |
| Scoring weights | `config.json` → `weights` |
| Account size & risk per trade | `config.json` → `risk` |
| Minimum score threshold | `config.json` → `selection.min_score` |
| Schedule times | `.github/workflows/*.yml` cron expressions |
| Gemini model | `GEMINI_MODEL` secret or `.env` |

---

## Project structure

```
├── briefing.py          # Pre-market & deep-analysis briefings (Gemini)
├── predict.py           # Single-stock intraday + weekly prediction (Gemini)
├── bot.py               # Telegram polling bot
├── daily_scan.py        # Pre-market stock selection & trade planning
├── eod_summary.py       # EOD performance check
│
├── tech_analysis.py     # RSI, MACD, ATR, support/resistance, full technical fetch
├── fundamentals.py      # yfinance fundamentals fetch + scoring
├── scoring.py           # Multi-factor stock scoring engine
├── signals.py           # Setup detection + entry/exit plan generation
├── risk.py              # Position sizing + quality assessment
├── formatter.py         # Telegram message assembly
├── telegram_utils.py    # Message splitting (4096 char limit)
│
├── config.json          # Universe, weights, risk params
├── requirements.txt
├── .env.example
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
