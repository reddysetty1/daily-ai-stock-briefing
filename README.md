# 📊 Daily Pre-Market Stock Briefing Bot

A fully automated morning stock briefing sent to Telegram every weekday before market open.
Powered by **Google Gemini AI** + **live market data** via yfinance, scheduled free on **GitHub Actions**.

No server. No cost. Just wake up to your briefing.

---

## What it sends every morning

```
📊 Tuesday, May 19, 2026 | Pre-Market Briefing

— MARKET SNAPSHOT —
📈 SPY: $738.65 down 0.03% | QQQ: $705.88 down 0.10%
📉 NVDA: $222.32 down 0.19% | TSLA: $409.99 up 0.13%
🌍 Mood: Cautious — Investors await key inflation data and Fed commentary.

— TODAY'S OUTLOOK —
Markets are trading flat as traders digest recent gains. Expect sector
rotation into defensive plays while tech remains range-bound near highs.
Watch SPY support at 735 and resistance at 742.

— TOP ANALYST MOVES —
🟢 BUY: Apple ($AAPL) | Now: $297.84 | Target: $340 by May 2027 | Strong services growth and AI integration.
🟢 BUY: Microsoft ($MSFT) | Now: $423.54 | Target: $480 by Dec 2026 | Cloud demand resilient despite macro headwinds.
🔴 SELL: NVIDIA ($NVDA) | Now: $222.32 | Downside: $195 by Aug 2026 | Valuation concerns after massive rally.
🔴 SELL: Amazon ($AMZN) | Now: $264.86 | Downside: $230 by Nov 2026 | Rising logistics costs hurting margins.

— HOT NEWS —
🇺🇸 White House signals new executive order targeting AI infrastructure investment.
🌐 European markets soften as regional manufacturing data misses expectations.
💼 Retail earnings season kicks off with focus on consumer spending resilience.

⚠️ Risk of the Day: Unexpected spike in bond yields could trigger sudden tech sell-off.
```

---

## Tech Stack

- **[Google Gemini API](https://aistudio.google.com)** — AI-generated briefing and analyst insights
- **[yfinance](https://github.com/ranaroussi/yfinance)** — Free real-time stock price data
- **[Telegram Bot API](https://core.telegram.org/bots/api)** — Message delivery
- **[GitHub Actions](https://github.com/features/actions)** — Free cloud cron scheduler (no server needed)
- **Python 3.11+**

---

## How it works

```
Every weekday at 7:00 AM IST
        ↓
GitHub Actions wakes up (free)
        ↓
yfinance fetches live prices for SPY, QQQ, NVDA, AAPL, TSLA, MSFT, AMZN
        ↓
Gemini AI generates the formatted briefing using live data
        ↓
Telegram Bot delivers it to your chat
```

---

## Fork and run it yourself

### 1. Get your API keys

| Key | Where to get it |
|-----|----------------|
| `GEMINI_API_KEY` | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) — free |
| `TELEGRAM_BOT_TOKEN` | Message `@BotFather` on Telegram → `/newbot` |
| `TELEGRAM_CHAT_ID` | Message your bot, then call `https://api.telegram.org/bot<TOKEN>/getUpdates` |

### 2. Fork this repo

Click the **Fork** button at the top of this page.

### 3. Add GitHub Secrets

Go to your forked repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these three secrets:

| Secret name | Value |
|-------------|-------|
| `GEMINI_API_KEY` | Your Gemini API key from [AI Studio](https://aistudio.google.com/app/apikey) |
| `GEMINI_MODEL` | Model name e.g. `gemini-3.1-flash-lite`, `gemini-2.0-flash`, `gemini-1.5-pro` |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

### 4. Enable GitHub Actions

Go to the **Actions** tab in your forked repo and click **"I understand my workflows, go ahead and enable them"**.

That's it. The workflow runs automatically every weekday at 7:00 AM IST (01:30 UTC).

### Test it manually

Go to **Actions → Daily Pre-Market Stock Briefing → Run workflow** to trigger it instantly.

---

## Local development

```bash
git clone https://github.com/YOUR_USERNAME/daily-stock-briefing
cd daily-stock-briefing

pip install -r requirements.txt

cp .env.example .env
# Fill in your keys in .env

python test_run.py          # preview only
python test_run.py --send   # preview + send to Telegram
```

---

## Customize

- **Tickers** — Edit the `TICKERS` dict in `briefing.py` to track different stocks
- **Schedule** — Edit the cron in `.github/workflows/daily_briefing.yml` ([crontab.guru](https://crontab.guru) helps)
- **Timezone reference** — `01:30 UTC` = `7:00 AM IST`. Adjust for your timezone
- **Gemini model** — Change `GEMINI_MODEL` in `.env` or the workflow file

---

## License

MIT — free to use, fork, and modify.
