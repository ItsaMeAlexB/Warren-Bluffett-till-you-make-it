#!/usr/bin/env python3
"""market.py - fetch per-ticker data from yfinance (info, price history and
annual financial statements) plus FX rates for base/local currency maths."""
import time

import yfinance as yf

from config import FETCH_DELAY

_fx_cache = {}


def fetch(ticker):
    """Return (info, price_df, statements) where statements = (income, bal, cf).
    A short delay is enforced between tickers to be fair to the free API."""
    try:
        tk = yf.Ticker(ticker)
    except Exception:
        return {}, None, (None, None, None)
    try:
        info = tk.info or {}
    except Exception:
        info = {}
    try:
        price_df = tk.history(period="1y", interval="1d", auto_adjust=True)
    except Exception:
        price_df = None
    try:
        income = tk.financials
        bal = tk.balance_sheet
        cf = tk.cashflow
    except Exception:
        income = bal = cf = None
    time.sleep(FETCH_DELAY)
    return info, price_df, (income, bal, cf)


def meta(info, ticker):
    return {
        "company": info.get("shortName") or info.get("longName") or ticker,
        "currency": (info.get("currency") or "USD"),
        "country": info.get("country") or "",
        "industry": info.get("industry") or info.get("sector") or "",
        "price": info.get("currentPrice") or info.get("regularMarketPrice")
                 or info.get("previousClose"),
    }


def fx_rate(base, local):
    """Local currency per 1 base (e.g. base=USD,local=EUR -> USDEUR=X)."""
    if not base or not local or base == local:
        return 1.0
    key = (base, local)
    if key in _fx_cache:
        return _fx_cache[key]
    try:
        t = yf.Ticker(f"{base}{local}=X")
        r = t.fast_info.last_price
        r = float(r) if r else None
    except Exception:
        r = None
    if not r or r <= 0:
        r = 1.0
    _fx_cache[key] = r
    return r


def base_to_local(base, local, amount):
    return amount * fx_rate(base, local)


def local_to_base(base, local, amount):
    r = fx_rate(base, local)
    return (amount / r) if r else 0.0
