"""
universe.py — Dynamic index constituent loader.

Sources:
  • S&P 500    — Wikipedia
  • NASDAQ 100 — Wikipedia
  • Dow Jones  — Wikipedia
  • NYSE       — NASDAQ Trader FTP (otherlisted.txt, covers NYSE + AMEX)
  • NASDAQ All — NASDAQ Trader FTP (nasdaqlisted.txt)

Results are cached locally in universe_cache.json and refreshed weekly.
Returns a dict: { ticker: { "name": str, "sector": str, "index": [str] } }
"""

import json
import logging
import os
import time
from datetime import datetime

log = logging.getLogger(__name__)

CACHE_FILE   = os.path.join(os.path.dirname(__file__), "universe_cache.json")
CACHE_TTL    = 7 * 24 * 3600   # refresh weekly

# Sector name → best tracking ETF
SECTOR_ETF = {
    "Technology":               "QQQ",
    "Information Technology":   "QQQ",
    "Communication Services":   "QQQ",
    "Consumer Discretionary":   "XLY",
    "Consumer Staples":         "XLP",
    "Health Care":              "XLV",
    "Financials":               "XLF",
    "Industrials":              "XLI",
    "Energy":                   "XLE",
    "Utilities":                "XLU",
    "Real Estate":              "XLRE",
    "Materials":                "XLB",
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _wiki_tables(url: str):
    """Fetch Wikipedia page and parse HTML tables with pandas."""
    import io, requests, pandas as pd
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def _fetch_sp500() -> dict:
    """Scrape S&P 500 constituents from Wikipedia."""
    tables = _wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    df = tables[0]
    result = {}
    for _, row in df.iterrows():
        ticker  = str(row.get("Symbol", "")).replace(".", "-").strip()
        name    = str(row.get("Security", "")).strip()
        sector  = str(row.get("GICS Sector", "")).strip()
        if ticker and name and ticker != "nan":
            result[ticker] = {"name": name, "sector": sector, "index": ["S&P 500"]}
    log.info("S&P 500: %d tickers", len(result))
    return result


def _fetch_nasdaq100() -> dict:
    """Scrape NASDAQ 100 constituents from Wikipedia."""
    tables = _wiki_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
    df = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any(k in cols for k in ("ticker", "symbol")):
            df = t
            break
    if df is None:
        log.warning("NASDAQ 100 table not found")
        return {}
    col = next(c for c in df.columns if str(c).lower() in ("ticker", "symbol"))
    name_col = next((c for c in df.columns if "company" in str(c).lower() or "name" in str(c).lower()), None)
    result = {}
    for _, row in df.iterrows():
        ticker = str(row[col]).replace(".", "-").strip()
        name   = str(row[name_col]).strip() if name_col else ticker
        if ticker and ticker != "nan":
            result[ticker] = {"name": name, "sector": "", "index": ["NASDAQ 100"]}
    log.info("NASDAQ 100: %d tickers", len(result))
    return result


def _fetch_dow() -> dict:
    """Scrape Dow Jones 30 constituents from Wikipedia."""
    tables = _wiki_tables("https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average")
    df = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any(k in cols for k in ("symbol", "ticker")):
            df = t
            break
    if df is None:
        log.warning("Dow Jones table not found")
        return {}
    col = next(c for c in df.columns if str(c).lower() in ("symbol", "ticker"))
    name_col = next((c for c in df.columns if "company" in str(c).lower() or "name" in str(c).lower()), None)
    result = {}
    for _, row in df.iterrows():
        ticker = str(row[col]).replace(".", "-").strip()
        name   = str(row[name_col]).strip() if name_col else ticker
        if ticker and ticker != "nan" and len(ticker) <= 5:
            result[ticker] = {"name": name, "sector": "", "index": ["Dow Jones"]}
    log.info("Dow Jones: %d tickers", len(result))
    return result


def _fetch_sec_all() -> dict:
    """
    Fetch all US exchange-listed companies from SEC EDGAR.
    Source: https://www.sec.gov/files/company_tickers_exchange.json
    Covers NYSE, NASDAQ, NYSE Arca, NYSE American (AMEX), and more.
    ~10,000+ companies — the most comprehensive free source available.
    """
    import requests
    url  = "https://www.sec.gov/files/company_tickers_exchange.json"
    # SEC requires a descriptive User-Agent per their policy
    hdrs = {"User-Agent": "StockAnalysisDashboard/1.0 (personal project; contact@example.com)"}
    resp = requests.get(url, headers=hdrs, timeout=30)
    resp.raise_for_status()
    data   = resp.json()
    fields = data["fields"]   # ['cik', 'name', 'ticker', 'exchange']
    result = {}
    for row in data["data"]:
        entry    = dict(zip(fields, row))
        ticker   = str(entry.get("ticker", "")).strip().upper()
        name     = str(entry.get("name",   "")).strip()
        exchange = str(entry.get("exchange", "")).strip()
        if not ticker or len(ticker) > 6:
            continue
        if not ticker.replace("-", "").isalpha():
            continue
        # Map SEC exchange labels to friendly names
        idx = {
            "NYSE":         "NYSE",
            "Nasdaq":       "NASDAQ",
            "NYSE Arca":    "NYSE",
            "NYSE American":"NYSE",
            "CBOE":         "CBOE",
        }.get(exchange, exchange)
        result[ticker] = {"name": name, "sector": "", "index": [idx]}
    log.info("SEC EDGAR (all exchanges): %d tickers", len(result))
    return result


def _build_universe() -> dict:
    """
    Merge all sources, deduplicate.
    Priority for sector/name: S&P 500 > NASDAQ 100 > Dow > NYSE > NASDAQ All
    """
    universe = {}

    # Ordered so richer data (Wikipedia) fills in first;
    # SEC covers everything else (NYSE + full NASDAQ)
    sources = [
        _fetch_sp500,
        _fetch_nasdaq100,
        _fetch_dow,
        _fetch_sec_all,
    ]

    for fetch_fn in sources:
        try:
            batch = fetch_fn()
            for ticker, info in batch.items():
                if ticker in universe:
                    universe[ticker]["index"] = list(set(universe[ticker]["index"] + info["index"]))
                    if not universe[ticker]["sector"] and info.get("sector"):
                        universe[ticker]["sector"] = info["sector"]
                    if universe[ticker]["name"] == ticker and info["name"] != ticker:
                        universe[ticker]["name"] = info["name"]
                else:
                    universe[ticker] = info
        except Exception as e:
            log.warning("Fetch failed (%s): %s", fetch_fn.__name__, e)

    # Clean up: keep only valid tickers (1-5 alpha chars, with optional hyphen for share classes)
    def valid_ticker(t):
        clean = t.replace("-", "")
        return 1 <= len(t) <= 6 and clean.isalpha()

    universe = {t: v for t, v in universe.items() if valid_ticker(t)}

    log.info("Combined universe: %d unique tickers", len(universe))
    return universe


def _load_cache() -> dict | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - data.get("_fetched_at", 0)
        if age > CACHE_TTL:
            log.info("Universe cache expired (%.0f days old) — refreshing", age / 86400)
            return None
        return data.get("universe", {})
    except Exception as e:
        log.warning("Cache read failed: %s", e)
        return None


def _save_cache(universe: dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"_fetched_at": time.time(), "universe": universe}, f)
        log.info("Universe cached: %d tickers → %s", len(universe), CACHE_FILE)
    except Exception as e:
        log.warning("Cache write failed: %s", e)


def get_universe(force_refresh: bool = False) -> dict:
    """
    Return the full universe dict.
    Uses cache if fresh; re-fetches from Wikipedia otherwise.
    Each value: { "name": str, "sector": str, "index": [str] }
    """
    if not force_refresh:
        cached = _load_cache()
        if cached:
            log.info("Universe loaded from cache: %d tickers", len(cached))
            return cached

    log.info("Fetching index constituents from Wikipedia…")
    universe = _build_universe()
    _save_cache(universe)
    return universe


def get_sector_etf(sector: str) -> str:
    """Map a GICS sector name to its best tracking ETF."""
    return SECTOR_ETF.get(sector, "SPY")


def get_tickers(universe: dict) -> list:
    """Return sorted list of tickers."""
    return sorted(universe.keys())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    u = get_universe(force_refresh=True)
    by_index = {}
    for t, v in u.items():
        for idx in v["index"]:
            by_index.setdefault(idx, []).append(t)
    for idx, tickers in by_index.items():
        print(f"{idx}: {len(tickers)} stocks")
    print(f"Total unique: {len(u)}")
