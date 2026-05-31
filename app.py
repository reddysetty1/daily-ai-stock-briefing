"""
app.py — Local web dashboard for stock analysis.
Run with: python app.py
Opens at: http://localhost:5000
"""

import io, sys, os, json, threading, time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from dotenv import load_dotenv

load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

app = Flask(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
CONFIG_PATH   = os.path.join(os.path.dirname(__file__), "config.json")
GEMINI_MODEL  = os.getenv("GEMINI_MODEL",  "gemini-3.1-flash-lite")
GEMINI_KEY    = os.getenv("GEMINI_API_KEY", "")
TG_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",   "")

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
def index():
    return render_template("index.html", universe=UNIVERSE)


@app.route("/api/market", methods=["GET"])
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


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """
    Run the full daily scan (all 40 stocks).
    Streams progress as Server-Sent Events.
    """
    def generate():
        try:
            import queue, threading
            import yfinance as yf
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from tech_analysis  import fetch_full_technical
            from fundamentals   import fetch_fundamentals
            from scoring        import score_stock
            from signals        import detect_setup, generate_entry_plan, generate_exit_plan
            from risk           import calculate_position_size, assess_setup_quality
            from formatter      import format_ranked_summary, format_stock_detail
            from telegram_utils import send_messages

            weights  = CONFIG["weights"]
            risk_cfg = CONFIG["risk"]
            tickers  = list(_universe_full.keys())
            total    = len(tickers)

            # Market context
            yield f"data: {json.dumps({'type':'status','msg':'Fetching market context…'})}\n\n"
            market_data = {}
            for etf in ["SPY", "QQQ", "IWM"]:
                try:
                    info  = yf.Ticker(etf).fast_info
                    price = round(float(info.last_price), 2)
                    prev  = float(info.previous_close)
                    chg   = round((price - prev) / prev * 100, 2)
                    market_data[etf] = {"price": price, "day_change_pct": chg}
                except:
                    market_data[etf] = {"price": 0.0, "day_change_pct": 0.0}

            yield f"data: {json.dumps({'type':'market','data':market_data})}\n\n"
            yield f"data: {json.dumps({'type':'status','msg':f'Scanning {total} stocks in parallel…'})}\n\n"

            def score_one(ticker):
                tech = fetch_full_technical(ticker)
                fund = fetch_fundamentals(ticker)
                sector = fund.get("sector") or _universe_full.get(ticker, {}).get("sector", "")
                sm = {ticker: get_sector_etf(sector)}
                sc, bd = score_stock(ticker, tech, fund, market_data, sm, weights)
                return {"ticker": ticker, "score": sc, "tech": tech, "fund": fund, "breakdown": bd}

            # Score all stocks with thread pool, stream progress via a queue
            result_q   = queue.Queue()
            scored     = []
            done_count = [0]

            def worker(t):
                try:
                    r = score_one(t)
                    result_q.put(("ok", t, r))
                except Exception as e:
                    result_q.put(("err", t, str(e)))

            executor = ThreadPoolExecutor(max_workers=12)
            futs = [executor.submit(worker, t) for t in tickers]

            while done_count[0] < total:
                try:
                    kind, ticker, payload = result_q.get(timeout=60)
                    done_count[0] += 1
                    i = done_count[0]
                    if kind == "ok":
                        scored.append(payload)
                        yield f"data: {json.dumps({'type':'scored','ticker':ticker,'score':round(payload['score'],1),'i':i,'total':total,'trend':payload['tech'].get('trend',''),'rsi':round(payload['tech'].get('rsi',0),1)})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type':'scored','ticker':ticker,'score':0,'i':i,'total':total,'error':payload})}\n\n"
                except Exception:
                    break
            executor.shutdown(wait=False)

            scored.sort(key=lambda x: x["score"], reverse=True)

            sel     = CONFIG["selection"]
            eligible = [s for s in scored if s["score"] >= sel["min_score"]]
            picks_raw = eligible[:sel["top_n_max"]] or scored[:sel["top_n_min"]]

            yield f"data: {json.dumps({'type':'status','msg':f'Building trade plans for {len(picks_raw)} picks…'})}\n\n"

            picks = []
            client = _gemini_client()
            for item in picks_raw:
                ticker = item["ticker"]
                tech, fund = item["tech"], item["fund"]
                setup = detect_setup(tech)
                ep    = generate_entry_plan(tech, setup)
                ex    = generate_exit_plan(tech, ep, setup, risk_cfg)
                pos   = calculate_position_size(
                    ep.get("entry_low") or tech["current_price"],
                    ex["stop"], risk_cfg["account_size"],
                    risk_cfg["risk_pct_per_trade"], risk_cfg["max_position_pct"]
                )
                quality, flags = assess_setup_quality(ex["rr1"], ex["rr2"], risk_cfg["min_risk_reward"], setup, tech)
                narrative = ""
                if client:
                    try:
                        from google.genai import types
                        prompt = (
                            f"2-3 sentence trade rationale for {ticker}. "
                            f"Setup:{setup} Trend:{tech.get('trend')} RSI:{tech.get('rsi')} "
                            f"Entry:${ep.get('entry_low')} Stop:${ex['stop']} T1:${ex['t1']} RR:{ex['rr1']}. "
                            f"Under 60 words plain text."
                        )
                        resp = client.models.generate_content(
                            model=GEMINI_MODEL, contents=prompt,
                            config=__import__('google.genai',fromlist=['types']).types.GenerateContentConfig(temperature=0.4, max_output_tokens=120)
                        )
                        narrative = resp.text.strip()
                    except:
                        pass

                picks.append({
                    "ticker": ticker, "score": item["score"], "setup": setup,
                    "tech": tech, "fund": fund, "breakdown": item["breakdown"],
                    "quality": quality, "flags": flags, "narrative": narrative,
                    "entry": {"entry_plan": ep, "exit_plan": ex, "position": pos},
                })

            # Send to Telegram
            if TG_TOKEN and TG_CHAT_ID:
                yield f"data: {json.dumps({'type':'status','msg':'Sending to Telegram…'})}\n\n"
                summary = format_ranked_summary(picks, market_data, total)
                send_messages(TG_TOKEN, TG_CHAT_ID, summary)
                for p in picks:
                    msg = format_stock_detail(p, risk_cfg["account_size"])
                    send_messages(TG_TOKEN, TG_CHAT_ID, msg)

            # Final result
            result_picks = []
            for p in picks:
                ep2 = p["entry"]["entry_plan"]
                ex2 = p["entry"]["exit_plan"]
                result_picks.append({
                    "ticker":    p["ticker"],
                    "name":      p["fund"].get("name", p["ticker"]),
                    "score":     round(p["score"], 1),
                    "setup":     p["setup"],
                    "quality":   p["quality"],
                    "narrative": p["narrative"],
                    "price":     p["tech"]["current_price"],
                    "change":    p["tech"]["day_change_pct"],
                    "entry_low": ep2.get("entry_low"),
                    "entry_high":ep2.get("entry_high"),
                    "stop":      ex2["stop"],
                    "t1":        ex2["t1"],
                    "t2":        ex2["t2"],
                    "rr1":       ex2["rr1"],
                    "flags":     p["flags"],
                })

            yield f"data: {json.dumps({'type':'done','picks':result_picks,'total_scanned':total})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type':'error','msg':str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import webbrowser
    print("🚀 Starting Stock Analysis Dashboard...")
    print("📊 Opening http://localhost:5000")
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(debug=False, port=5000, threaded=True)
