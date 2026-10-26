#!/usr/bin/env python3
"""eulerpool_fund.py - rich fundamentals from eulerpool (optional primary).

eulerpool accepts the same Yahoo-style tickers (e.g. LONN.SW, CS.PA, 2171.HK)
so our watchlist carries over. We pull its KPI bundle (revenue, margins,
growth, AAQS) plus Piotroski/Altman quality scores, throttled at 1 s per call
like every other provider. Any failure/404 returns None so the caller falls
back to yfinance.
"""
import time

import requests

from config import EULER_TICKER_MAP, FETCH_DELAY

_BASE = "https://api.eulerpool.com/api/1"

# Set to True when the last fetch exhausted every configured key (AllKeysFailed);
# callers fall back to yfinance and may tag the source 'eulerpool_all_keys_failed'.
_all_failed = False


def all_keys_failed():
    """True if the most recent eulerpool fetch exhausted all keys (quota/auth)."""
    return _all_failed


def _euler_symbol(ticker):
    return EULER_TICKER_MAP.get(ticker, ticker)


def _get(path, identifier):
    global _all_failed
    from keys import AllKeysFailed, call_with_failover, eulerpool_keys
    keys = eulerpool_keys()
    if not keys:
        return None

    def req(key):
        return requests.get(f"{_BASE}/{path}/{identifier}?token={key}", timeout=10)

    try:
        r, _ = call_with_failover(keys, req, f"eulerpool/{path}", ticker=identifier)
    except AllKeysFailed:
        _all_failed = True
        return None
    except RuntimeError:
        return None
    except requests.RequestException:
        return None              # network error, not quota
    _all_failed = False
    time.sleep(FETCH_DELAY)          # 1 s spacing, same as yfinance/news
    return r.json()


def search(name):
    """Best-effort company-name lookup on eulerpool, returning a ticker or None.
    Falls back to yfinance for the row if this can't resolve."""
    from keys import eulerpool_keys
    keys = eulerpool_keys()
    if not keys or not name:
        return None
    for key in keys:
        for path in ("equity/ticker", "equity/search", "search"):
            url = f"{_BASE}/{path}/{name}?token={key}"
            try:
                r = requests.get(url, timeout=8)
                time.sleep(FETCH_DELAY)
                if r.status_code == 200:
                    j = r.json()
                    if isinstance(j, dict):
                        t = j.get("ticker")
                        if t:
                            return t
            except requests.RequestException:
                continue
    return None


def fetch(ticker):
    """Return {'kpi': row} (one call). The KPI row already carries ratios,
    margins, growth, Piotroski and Altman, so no extra calls are needed."""
    kpi = _get("equity/kpi", _euler_symbol(ticker))
    if not isinstance(kpi, dict):
        return None
    rows = kpi.get("kpi") or [{}]
    return {"kpi": rows[0] or {}}


def quotes_batch(tickers):
    """Batch latest prices for many tickers in ONE quota unit
    (GET /market/quotes/latest?stocks=A,B,C,...). Returns {ticker: price}.

    The single-equity routes (kpi, quarterly statements) have no dedicated
    multi-identifier GET here; the documented batch route for fundamentals is
    POST /excel-addin/batch (max 100 requests in one call), which needs the
    Excel-function names mapped - flagged, not yet wired."""
    from keys import AllKeysFailed, call_with_failover, eulerpool_keys
    keys = eulerpool_keys()
    if not keys or not tickers:
        return {}
    syms = ",".join(dict.fromkeys(tickers))

    def req(key):
        return requests.get(
            f"{_BASE}/market/quotes/latest?stocks={syms}&token={key}", timeout=12)

    try:
        r, _ = call_with_failover(keys, req, "market/quotes/latest", ticker=syms)
    except (AllKeysFailed, RuntimeError):
        return {}
    j = r.json() or {}
    out = {}
    if isinstance(j, dict):
        for tk, v in j.items():
            if isinstance(v, dict):
                v = v.get("price") or v.get("close") or v.get("value")
            if isinstance(v, (int, float)):
                out[tk] = v
    return out


def candles_df(ticker):
    """Daily OHLC history from eulerpool as a DataFrame (Close/Open), or None.
    This is the PRIMARY price source; yfinance is the fallback."""
    d = _get("equity/candles", _euler_symbol(ticker))
    if not isinstance(d, list) or not d:
        return None
    import pandas as pd
    rows = []
    for c in d:
        ts = c.get("timestamp"); op = c.get("open"); cl = c.get("close")
        if ts is None or cl is None:
            continue
        rows.append((pd.to_datetime(ts, unit="ms"), op, cl))
    if len(rows) < 2:
        return None
    df = pd.DataFrame(rows, columns=["date", "Open", "Close"]).set_index("date")
    return df.sort_index()
