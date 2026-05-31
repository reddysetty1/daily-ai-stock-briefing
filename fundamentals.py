"""
fundamentals.py — Fundamental analysis via yfinance ticker.info.
Gracefully handles missing fields. Returns a scored dict.
"""

import logging
import yfinance as yf

log = logging.getLogger(__name__)


def fetch_fundamentals(ticker: str) -> dict:
    """
    Fetch fundamental data from yfinance.
    All fields default to None if unavailable — callers must handle None.
    """
    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        log.warning("fundamentals fetch failed for %s: %s", ticker, e)
        return _empty(ticker)

    def safe(key):
        val = info.get(key)
        return val if val not in (None, "N/A", 0, "") else None

    market_cap = safe("marketCap")
    fcf        = safe("freeCashflow")
    total_debt = safe("totalDebt")
    total_cash = safe("totalCash")

    fcf_yield      = (fcf / market_cap)          if (fcf        and market_cap) else None
    net_cash       = (total_cash - total_debt)   if (total_cash and total_debt) else None
    debt_to_equity = safe("debtToEquity")

    return {
        "name":             info.get("longName", ticker),
        "sector":           info.get("sector",   "Unknown"),
        "industry":         info.get("industry", "Unknown"),
        "summary":          (info.get("longBusinessSummary") or "")[:250],
        # Growth
        "revenue_growth":   safe("revenueGrowth"),    # e.g. 0.22 = 22%
        "earnings_growth":  safe("earningsGrowth"),
        # Margins
        "gross_margin":     safe("grossMargins"),
        "operating_margin": safe("operatingMargins"),
        "profit_margin":    safe("profitMargins"),
        # Valuation
        "trailing_pe":      safe("trailingPE"),
        "forward_pe":       safe("forwardPE"),
        "price_to_sales":   safe("priceToSalesTrailing12Months"),
        "ev_to_ebitda":     safe("enterpriseToEbitda"),
        # Balance sheet
        "fcf_yield":        fcf_yield,
        "debt_to_equity":   debt_to_equity,
        "net_cash":         net_cash,
        # Quality
        "roe":              safe("returnOnEquity"),
        "beta":             safe("beta"),
        "recommendation":   (info.get("recommendationKey") or "").lower(),
        # Earnings calendar
        "earnings_timestamp": safe("earningsTimestamp"),
    }


def _empty(ticker: str) -> dict:
    return {
        "name": ticker, "sector": "Unknown", "industry": "Unknown", "summary": "",
        "revenue_growth": None, "earnings_growth": None, "gross_margin": None,
        "operating_margin": None, "profit_margin": None, "trailing_pe": None,
        "forward_pe": None, "price_to_sales": None, "ev_to_ebitda": None,
        "fcf_yield": None, "debt_to_equity": None, "net_cash": None,
        "roe": None, "beta": None, "recommendation": "", "earnings_timestamp": None,
    }


def score_fundamentals(fund: dict) -> tuple:
    """
    Score fundamentals 0-100. Returns (score, breakdown_dict).
    Missing fields are skipped (neutral, not penalised).
    """
    score = 0
    breakdown = {}

    # Revenue growth (0-25)
    rg = fund.get("revenue_growth")
    if rg is not None:
        if rg > 0.20:   pts = 25
        elif rg > 0.10: pts = 18
        elif rg > 0.05: pts = 10
        elif rg > 0:    pts = 5
        else:           pts = 0
        score += pts
        breakdown["revenue_growth"] = pts
    else:
        breakdown["revenue_growth"] = "n/a"

    # EPS growth (0-20)
    eg = fund.get("earnings_growth")
    if eg is not None:
        if eg > 0.20:   pts = 20
        elif eg > 0.10: pts = 14
        elif eg > 0:    pts = 7
        else:           pts = 0
        score += pts
        breakdown["earnings_growth"] = pts
    else:
        breakdown["earnings_growth"] = "n/a"

    # Gross margin (0-15)
    gm = fund.get("gross_margin")
    if gm is not None:
        if gm > 0.50:   pts = 15
        elif gm > 0.30: pts = 10
        elif gm > 0.15: pts = 5
        else:           pts = 0
        score += pts
        breakdown["gross_margin"] = pts
    else:
        breakdown["gross_margin"] = "n/a"

    # FCF yield (0-20)
    fcf = fund.get("fcf_yield")
    if fcf is not None:
        if fcf > 0.04:  pts = 20
        elif fcf > 0.02: pts = 12
        elif fcf > 0:   pts = 5
        else:           pts = 0
        score += pts
        breakdown["fcf_yield"] = pts
    else:
        breakdown["fcf_yield"] = "n/a"

    # P/E valuation (0-10)
    pe = fund.get("trailing_pe") or fund.get("forward_pe")
    if pe is not None and pe > 0:
        if pe < 15:     pts = 10
        elif pe < 25:   pts = 7
        elif pe < 35:   pts = 4
        elif pe < 50:   pts = 2
        else:           pts = 0
        score += pts
        breakdown["pe_valuation"] = pts
    else:
        breakdown["pe_valuation"] = "n/a"

    # Balance sheet (0-10)
    de = fund.get("debt_to_equity")
    if de is not None:
        if de < 30:    pts = 10
        elif de < 80:  pts = 6
        elif de < 150: pts = 3
        else:          pts = 0
        score += pts
        breakdown["balance_sheet"] = pts
    else:
        breakdown["balance_sheet"] = "n/a"

    # Analyst recommendation bonus (0-10, capped)
    rec = fund.get("recommendation", "")
    if rec in ("strong_buy", "buy"):
        score += 10
        breakdown["recommendation"] = 10
    elif rec in ("hold",):
        breakdown["recommendation"] = 0
    else:
        breakdown["recommendation"] = 0

    # Clamp to 0-100
    score = min(100, max(0, score))
    return score, breakdown


def format_fundamentals_summary(fund: dict) -> str:
    """One-line fundamental summary for Telegram messages."""
    parts = []
    if fund.get("revenue_growth") is not None:
        parts.append(f"Rev +{fund['revenue_growth']*100:.0f}%")
    if fund.get("earnings_growth") is not None:
        parts.append(f"EPS +{fund['earnings_growth']*100:.0f}%")
    if fund.get("gross_margin") is not None:
        parts.append(f"GM {fund['gross_margin']*100:.0f}%")
    if fund.get("trailing_pe") is not None:
        parts.append(f"P/E {fund['trailing_pe']:.0f}x")
    elif fund.get("forward_pe") is not None:
        parts.append(f"fP/E {fund['forward_pe']:.0f}x")
    return " | ".join(parts) if parts else "Fundamentals data limited"
