"""
app.py — Stock analysis web dashboard.
Local:  python app.py  →  http://localhost:5000
Hosted: deployed on Render, accessible from any device.
Protected by APP_PASSWORD env var (set a password before hosting).
"""

import io, sys, os, json, threading, time, secrets
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, jsonify,
                   Response, stream_with_context, session, redirect, url_for)
from dotenv import load_dotenv

load_dotenv()
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass   # already wrapped (e.g. on Render)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

# ── Config ─────────────────────────────────────────────────────────────────────
CONFIG_PATH   = os.path.join(os.path.dirname(__file__), "config.json")
GEMINI_MODEL  = os.getenv("GEMINI_MODEL",  "gemini-3.1-flash-lite")
GEMINI_KEY    = os.getenv("GEMINI_API_KEY", "")
TG_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",   "")
APP_PASSWORD  = os.getenv("APP_PASSWORD", "")   # set this to protect the dashboard

# ── Auth helpers ───────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if APP_PASSWORD and not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["authenticated"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

# Dynamic universe — S&P 500 + NASDAQ 100 + Dow Jones (cached weekly)
from universe import get_universe, get_sector_etf
_universe_full = get_universe()          # {ticker: {name, sector, index}}
UNIVERSE = {t: v["name"] for t, v in _universe_full.items()}   # {ticker: name} for template


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gemini_client():
    if not GEMINI_KEY:
        return None
    from google import genai
    return genai.Client(api_key=GEMINI_KEY)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html", universe=UNIVERSE)


@app.route("/api/market", methods=["GET"])
@login_required
def api_market():
    """Quick SPY / QQQ / IWM snapshot."""
    try:
        import yfinance as yf
        result = {}
        for ticker in ["SPY", "QQQ", "IWM"]:
            info  = yf.Ticker(ticker).fast_info
            price = round(float(info.last_price), 2)
            prev  = float(info.previous_close)
            chg   = round((price - prev) / prev * 100, 2) if prev else 0.0
            result[ticker] = {"price": price, "change": chg}
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    """
    Full single-stock analysis.
    Body: { "ticker": "AAPL", "send_telegram": true }
    Returns full analysis dict + formatted message.
    """
    body   = request.get_json(force=True)
    ticker = body.get("ticker", "").strip().upper()
    send_tg = body.get("send_telegram", True)

    if not ticker:
        return jsonify({"ok": False, "error": "No ticker provided."})

    try:
        from tech_analysis import fetch_full_technical
        from fundamentals  import fetch_fundamentals
        from scoring       import score_stock
        from signals       import detect_setup, generate_entry_plan, generate_exit_plan
        from risk          import calculate_position_size, assess_setup_quality
        from formatter     import format_stock_detail
        from telegram_utils import send_messages

        market_data = {}
        import yfinance as yf
        for etf in ["SPY", "QQQ", "IWM"]:
            try:
                info  = yf.Ticker(etf).fast_info
                price = round(float(info.last_price), 2)
                prev  = float(info.previous_close)
                chg   = round((price - prev) / prev * 100, 2) if prev else 0.0
                market_data[etf] = {"price": price, "day_change_pct": chg}
            except:
                market_data[etf] = {"price": 0.0, "day_change_pct": 0.0}

        tech    = fetch_full_technical(ticker)
        fund    = fetch_fundamentals(ticker)
        weights  = CONFIG["weights"]
        risk_cfg = CONFIG["risk"]
        sector   = fund.get("sector") or _universe_full.get(ticker, {}).get("sector", "")
        sector_map = {ticker: get_sector_etf(sector)}

        score, bd = score_stock(ticker, tech, fund, market_data, sector_map, weights)
        setup     = detect_setup(tech)
        ep        = generate_entry_plan(tech, setup)
        ex        = generate_exit_plan(tech, ep, setup, risk_cfg)
        pos       = calculate_position_size(
            ep.get("entry_low") or tech["current_price"],
            ex["stop"], risk_cfg["account_size"],
            risk_cfg["risk_pct_per_trade"], risk_cfg["max_position_pct"]
        )
        quality, flags = assess_setup_quality(ex["rr1"], ex["rr2"], risk_cfg["min_risk_reward"], setup, tech)

        # Optional Gemini narrative
        narrative = ""
        client = _gemini_client()
        if client:
            try:
                from google.genai import types
                prompt = (
                    f"Write a concise 2-3 sentence trade rationale for {ticker}. "
                    f"Setup: {setup}, Trend: {tech.get('trend')}, RSI: {tech.get('rsi')}, "
                    f"MACD: {'Bullish' if tech.get('macd',0) > tech.get('macd_signal',0) else 'Bearish'}, "
                    f"Entry: ${ep.get('entry_low')} Stop: ${ex['stop']} T1: ${ex['t1']} R:R {ex['rr1']}. "
                    f"Under 60 words, plain text only."
                )
                resp = client.models.generate_content(
                    model=GEMINI_MODEL, contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=120)
                )
                narrative = resp.text.strip()
            except Exception as e:
                narrative = ""

        pick = {
            "ticker": ticker, "score": score, "setup": setup,
            "tech": tech, "fund": fund, "breakdown": bd,
            "quality": quality, "flags": flags, "narrative": narrative,
            "entry": {"entry_plan": ep, "exit_plan": ex, "position": pos},
        }

        tg_sent = False
        if send_tg and TG_TOKEN and TG_CHAT_ID:
            msg = format_stock_detail(pick, risk_cfg["account_size"])
            tg_sent = send_messages(TG_TOKEN, TG_CHAT_ID, msg)

        return jsonify({
            "ok": True,
            "ticker":    ticker,
            "name":      fund.get("name", ticker),
            "score":     score,
            "setup":     setup,
            "quality":   quality,
            "flags":     flags,
            "narrative": narrative,
            "tg_sent":   tg_sent,
            "tech": {
                "price":        tech["current_price"],
                "change":       tech["day_change_pct"],
                "trend":        tech["trend"],
                "rsi":          tech["rsi"],
                "macd_bullish": tech["macd"] > tech["macd_signal"],
                "vol_pct":      tech["vol_pct"],
                "vol_trend":    tech["vol_trend"],
                "attractiveness": tech["attractiveness"],
                "week52_high":  tech["week52_high"],
                "week52_low":   tech["week52_low"],
                "atr14":        tech["atr14"],
                "ma50":         tech["ma50"],
                "ma200":        tech["ma200"],
            },
            "fund": {
                "sector":          fund.get("sector"),
                "revenue_growth":  fund.get("revenue_growth"),
                "earnings_growth": fund.get("earnings_growth"),
                "gross_margin":    fund.get("gross_margin"),
                "trailing_pe":     fund.get("trailing_pe"),
                "beta":            fund.get("beta"),
                "recommendation":  fund.get("recommendation"),
            },
            "entry_plan": ep,
            "exit_plan":  ex,
            "position":   pos,
            "breakdown":  bd,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# Scope definitions — filters on the cached universe
SCOPES = {
    "sp500":   {"label": "S&P 500",              "filter": lambda v: "S&P 500"    in v["index"]},
    "top":     {"label": "S&P 500 + NASDAQ 100 + Dow", "filter": lambda v: any(i in v["index"] for i in ("S&P 500","NASDAQ 100","Dow Jones"))},
    "nasdaq":  {"label": "All NASDAQ",           "filter": lambda v: "NASDAQ"     in v["index"]},
    "nyse":    {"label": "All NYSE",             "filter": lambda v: "NYSE"        in v["index"]},
    "all":     {"label": "All Markets",          "filter": lambda v: True},
}

BATCH_SIZE = 20   # tickers per parallel batch — balances speed vs gevent responsiveness


@app.route("/api/scan")          # GET so proxies never buffer it
@login_required
def api_scan():
    """
    Stream scan progress as Server-Sent Events (GET endpoint).
    Query params:
      scope    = sp500 | top | nasdaq | nyse | all  (default: sp500)
      telegram = 1 | 0  (default: 1)
    """
    scope_key = request.args.get("scope", "sp500")
    send_tg   = request.args.get("telegram", "1") == "1"
    scope     = SCOPES.get(scope_key, SCOPES["sp500"])

    # Parse filter params from query string
    filters = {
        "min_price":      float(request.args.get("min_price",      5.0)),
        "max_price":      float(request.args.get("max_price",      0)) or None,
        "min_avg_volume": float(request.args.get("min_avg_volume", 500_000)),
        "min_rel_volume": float(request.args.get("min_rel_volume", 0.3)),
        "min_market_cap": float(request.args.get("min_market_cap", 300_000_000)),
    }

    def generate():
        try:
            import yfinance as yf
            from concurrent.futures import ThreadPoolExecutor
            from screener       import screen
            from tech_analysis  import fetch_full_technical
            from fundamentals   import fetch_fundamentals
            from scoring        import score_stock
            from signals        import detect_setup, generate_entry_plan, generate_exit_plan
            from risk           import calculate_position_size, assess_setup_quality
            from formatter      import format_ranked_summary, format_stock_detail
            from telegram_utils import send_messages

            weights  = CONFIG["weights"]
            risk_cfg = CONFIG["risk"]

            # Filter universe by scope
            universe_tickers = [t for t, v in _universe_full.items() if scope["filter"](v)]
            universe_total   = len(universe_tickers)

            def sse(payload: dict) -> str:
                return f"data: {json.dumps(payload)}\n\n"

            # ── Phase 1: Fast pre-filter ───────────────────────────────────────
            yield sse({"type": "phase", "phase": 1,
                       "msg": f"Phase 1 — Pre-filtering {universe_total} stocks…"})

            screened_done = [0]
            def on_progress(done, total, ticker):
                screened_done[0] = done

            passed, summaries = screen(universe_tickers, filters, workers=30,
                                       progress_cb=on_progress)
            total = len(passed)

            yield sse({"type": "screened",
                       "passed": total,
                       "total":  universe_total,
                       "msg":    f"✓ {total} stocks passed filters — running full analysis…"})

            if total == 0:
                yield sse({"type": "error",
                           "msg": "No stocks passed the filters. Try relaxing the thresholds."})
                return

            # ── Phase 2: Market context ────────────────────────────────────────
            yield sse({"type": "status", "msg": f"Fetching market context…"})

            market_data = {}
            for etf in ["SPY", "QQQ", "IWM"]:
                try:
                    info  = yf.Ticker(etf).fast_info
                    price = round(float(info.last_price), 2)
                    prev  = float(info.previous_close)
                    chg   = round((price - prev) / prev * 100, 2)
                    market_data[etf] = {"price": price, "day_change_pct": chg}
                except Exception:
                    market_data[etf] = {"price": 0.0, "day_change_pct": 0.0}

            yield sse({"type": "market", "data": market_data})

            def score_one(ticker):
                tech   = fetch_full_technical(ticker)
                fund   = fetch_fundamentals(ticker)
                sector = fund.get("sector") or _universe_full.get(ticker, {}).get("sector", "")
                sm     = {ticker: get_sector_etf(sector)}
                sc, bd = score_stock(ticker, tech, fund, market_data, sm, weights)
                return {"ticker": ticker, "score": sc, "tech": tech, "fund": fund, "breakdown": bd}

            # Score in fixed batches — yield between batches so gevent can flush the stream
            scored = []
            done   = 0
            for batch_start in range(0, total, BATCH_SIZE):
                batch = passed[batch_start: batch_start + BATCH_SIZE]
                with ThreadPoolExecutor(max_workers=min(BATCH_SIZE, len(batch))) as pool:
                    results = list(pool.map(score_one, batch, timeout=30))

                for r in results:
                    done += 1
                    if r:
                        scored.append(r)
                        yield sse({
                            "type":  "scored",
                            "ticker": r["ticker"],
                            "score":  round(r["score"], 1),
                            "i":     done,
                            "total": total,
                            "trend": r["tech"].get("trend", ""),
                            "rsi":   round(r["tech"].get("rsi", 0), 1),
                        })
                    else:
                        yield sse({"type": "scored", "ticker": "?", "score": 0, "i": done, "total": total})

                time.sleep(0)   # yield to gevent event loop — flushes buffered SSE events

            # Pick selection
            scored.sort(key=lambda x: x["score"], reverse=True)
            sel       = CONFIG["selection"]
            eligible  = [s for s in scored if s["score"] >= sel["min_score"]]
            picks_raw = eligible[: sel["top_n_max"]] or scored[: sel["top_n_min"]]

            yield sse({"type": "status", "msg": f"Building trade plans for {len(picks_raw)} picks…"})

            picks  = []
            client = _gemini_client()
            for item in picks_raw:
                ticker    = item["ticker"]
                tech, fund = item["tech"], item["fund"]
                setup     = detect_setup(tech)
                ep        = generate_entry_plan(tech, setup)
                ex        = generate_exit_plan(tech, ep, setup, risk_cfg)
                pos       = calculate_position_size(
                    ep.get("entry_low") or tech["current_price"],
                    ex["stop"], risk_cfg["account_size"],
                    risk_cfg["risk_pct_per_trade"], risk_cfg["max_position_pct"],
                )
                quality, flags = assess_setup_quality(
                    ex["rr1"], ex["rr2"], risk_cfg["min_risk_reward"], setup, tech
                )
                narrative = ""
                if client:
                    try:
                        from google.genai import types
                        prompt = (
                            f"2-3 sentence trade rationale for {ticker}. "
                            f"Setup:{setup} Trend:{tech.get('trend')} RSI:{tech.get('rsi')} "
                            f"Entry:${ep.get('entry_low')} Stop:${ex['stop']} "
                            f"T1:${ex['t1']} RR:{ex['rr1']}. Under 60 words plain text."
                        )
                        resp = client.models.generate_content(
                            model=GEMINI_MODEL, contents=prompt,
                            config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=120),
                        )
                        narrative = resp.text.strip()
                    except Exception:
                        pass

                picks.append({
                    "ticker": ticker, "score": item["score"], "setup": setup,
                    "tech": tech, "fund": fund, "breakdown": item["breakdown"],
                    "quality": quality, "flags": flags, "narrative": narrative,
                    "entry": {"entry_plan": ep, "exit_plan": ex, "position": pos},
                })

            # Telegram
            if send_tg and TG_TOKEN and TG_CHAT_ID:
                yield sse({"type": "status", "msg": "Sending to Telegram…"})
                send_messages(TG_TOKEN, TG_CHAT_ID,
                              format_ranked_summary(picks, market_data, total))
                for p in picks:
                    send_messages(TG_TOKEN, TG_CHAT_ID,
                                  format_stock_detail(p, risk_cfg["account_size"]))

            # Done — send final picks to UI
            result_picks = []
            for p in picks:
                ep2, ex2 = p["entry"]["entry_plan"], p["entry"]["exit_plan"]
                result_picks.append({
                    "ticker":     p["ticker"],
                    "name":       p["fund"].get("name", p["ticker"]),
                    "score":      round(p["score"], 1),
                    "setup":      p["setup"],
                    "quality":    p["quality"],
                    "narrative":  p["narrative"],
                    "price":      p["tech"]["current_price"],
                    "change":     p["tech"]["day_change_pct"],
                    "entry_low":  ep2.get("entry_low"),
                    "entry_high": ep2.get("entry_high"),
                    "stop":       ex2["stop"],
                    "t1":         ex2["t1"],
                    "t2":         ex2["t2"],
                    "rr1":        ex2["rr1"],
                    "flags":      p["flags"],
                })

            yield sse({"type": "done", "picks": result_picks, "total_scanned": total})

        except Exception as e:
            yield f"data: {json.dumps({'type':'error','msg':str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


if __name__ == "__main__":
    import webbrowser
    port = int(os.getenv("PORT", 5000))
    is_local = port == 5000
    print("🚀 Starting Stock Analysis Dashboard...")
    if is_local:
        print(f"📊 Opening http://localhost:{port}")
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
