"""
screener.py — Fast first-pass filter before full technical analysis.

Uses yfinance fast_info only (no history download) so each ticker
takes ~0.1-0.2s instead of ~2s. Filters out low-quality, illiquid,
or dormant names so full analysis only runs on stocks worth looking at.

Default filters (all configurable):
  min_price        $5       — avoids penny stocks / low-quality names
  min_avg_volume   500,000  — ensures easy entries and exits
  min_rel_volume   0.3      — stock must have at least 30% of normal volume today
  min_market_cap   $300M    — filters out micro-caps (set 0 to disable)
  max_price        None     — optional upper bound (e.g. 500 to avoid expensive names)
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

log = logging.getLogger(__name__)

DEFAULT_FILTERS = {
    "min_price":      5.0,
    "max_price":      None,
    "min_avg_volume": 500_000,
    "min_rel_volume": 0.3,
    "min_market_cap": 300_000_000,   # $300M
}


def _check_one(ticker: str, filters: dict) -> dict | None:
    """
    Quick fast_info check. Returns a summary dict if the ticker passes,
    or None if it fails any filter.
    """
    try:
        fi = yf.Ticker(ticker).fast_info

        price      = float(fi.last_price          or 0)
        avg_vol    = float(fi.three_month_average_volume or 0)
        last_vol   = float(fi.last_volume          or 0)
        market_cap = float(fi.market_cap           or 0)
        rel_vol    = (last_vol / avg_vol) if avg_vol > 0 else 0

        # ── Apply filters ──────────────────────────────────────────
        if price < filters["min_price"]:
            return None
        if filters["max_price"] and price > filters["max_price"]:
            return None
        if avg_vol < filters["min_avg_volume"]:
            return None
        if rel_vol < filters["min_rel_volume"]:
            return None
        if filters["min_market_cap"] and market_cap < filters["min_market_cap"]:
            return None

        return {
            "ticker":     ticker,
            "price":      round(price, 2),
            "avg_vol":    int(avg_vol),
            "last_vol":   int(last_vol),
            "rel_vol":    round(rel_vol, 2),
            "market_cap": int(market_cap),
        }

    except Exception as e:
        log.debug("Screener skip %s: %s", ticker, e)
        return None


def screen(tickers: list, filters: dict = None,
           workers: int = 30,
           progress_cb=None) -> tuple[list, list]:
    """
    Run the fast pre-filter on a list of tickers in parallel.

    Args:
        tickers     — full list to screen
        filters     — dict of filter params (falls back to DEFAULT_FILTERS for missing keys)
        workers     — thread pool size (30 is safe for yfinance fast_info)
        progress_cb — optional callable(done, total, ticker) for progress updates

    Returns:
        (passed, summaries)
        passed    — list of tickers that passed all filters
        summaries — list of dicts with {ticker, price, avg_vol, rel_vol, market_cap}
    """
    f = {**DEFAULT_FILTERS, **(filters or {})}

    passed    = []
    summaries = []
    total     = len(tickers)
    done      = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_to_ticker = {pool.submit(_check_one, t, f): t for t in tickers}
        for fut in as_completed(fut_to_ticker):
            done += 1
            result = fut.result()
            if result:
                passed.append(result["ticker"])
                summaries.append(result)
            if progress_cb:
                progress_cb(done, total, fut_to_ticker[fut])

    # Sort by relative volume descending — most "in play" first
    summaries.sort(key=lambda x: x["rel_vol"], reverse=True)
    passed = [s["ticker"] for s in summaries]

    log.info("Screener: %d/%d passed filters", len(passed), total)
    return passed, summaries
