#!/usr/bin/env python3
"""news.py - daily news sentiment with a reliability filter.

Source: stockdata.org (global news) attempted first, then the Yahoo Finance
search endpoint as a reliable fallback. Headlines are deduplicated, very short
or obvious auto "price moved %" blurbs dropped, then scored to a daily 0-10
value.

The change from day 2 to day 3 (today) is the news momentum; it is treated as
*significant* when it is larger in magnitude than the day 1 -> day 2 change
(noise filter). That significance and its sign drive the entry/exit rules.
"""
import datetime
import os
import re

import requests

from config import (STOCKDATA_API_KEY, STOCKDATA_API_KEY_2,
                    NEWS_DAYS_BOOTSTRAP, NEWS_NEWSCOUNT,
                    SENTIMENT_SD_STOCKDATA, SENTIMENT_SD_VADER)
import sentiment as sen

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
# Entity match confidence floor: entities below this are treated as ambiguous
# (co-occurrence / weak match) -> tone 0 for that entity, but that it still a
# StockData read (ambiguity, not failure). 0 = use every entity sentiment_score
# the API returns. A ~10 floor blocked valid matches (AAPL was ~9.4).
MATCH_SCORE_FLOOR = 0.0
_JUNK = re.compile(
    r"(Stock Moves|What You Should Know|Shares? (?:fall|rise|gain|drop|surge)"
    r"[^.]*%|Stock Sinks As|Stock Rises As|Weekly Stock|daily stock|"
    r"Insider (?:Sells|Buys)|ETF Inflow Alert|price target (?:raised|cut))",
    re.IGNORECASE)


def _is_junk(headline):
    if len(headline) < 24:
        return True
    return bool(_JUNK.search(headline))


def _clean(items):
    seen, out = set(), []
    for pt, title in items:
        if not title or _is_junk(title):
            continue
        norm = re.sub(r"[^a-z0-9]", "", title.lower())
        if norm in seen:
            continue
        seen.add(norm)
        out.append((int(pt), title))
    return out


def _parse_stockdata_time(t):
    """ISO-8601 published_at -> epoch seconds (UTC), or None."""
    if not t:
        return None
    try:
        s = str(t).replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _fetch_stockdata(ticker):
    """stockdata.org news for a symbol (global coverage, one call per ticker).
    Returns [(epoch, title)]; the free tier is ~100 calls/day so this pairs
    with the Yahoo fallback rather than being the sole source."""
    if not STOCKDATA_API_KEY:
        return []
    url = ("https://api.stockdata.org/v1/news/all"
           f"?symbols={ticker}&limit={NEWS_NEWSCOUNT}&api_token={STOCKDATA_API_KEY}")
    try:
        r = requests.get(url, headers=_UA, timeout=8)
        if r.status_code != 200:
            return []
        items = (r.json() or {}).get("data") or []
    except requests.RequestException:
        return []
    out = []
    for it in items:
        title = (it or {}).get("title")
        pt = _parse_stockdata_time((it or {}).get("published_at"))
        if title and pt is not None:
            out.append((int(pt), title))
    return out


def stockdata_tone(ticker, count=NEWS_NEWSCOUNT):
    """"Per-symbol sentiment from StockData news entity sentiment over the most
    recent fetched span (not windowed).

    tone = match_score-weighted mean of that symbol's `entities[].sentiment_score`
    (-1..+1). Returns (tone, scored_n); tone is None when no entity survives the
    match_score floor. Source routing (StockData vs VADER fallback) is decided by
    stockdata_windows(), not here. Uses the shared multi-key failover."""
    status, _ecode, arts = _stockdata_all(
        f"?symbols={ticker}&filter_entities=true&language=en&limit={count}",
        ticker=ticker)
    if status != 200:
        return None, 0
    wsum, w, n = 0.0, 0.0, 0
    for a in arts:
        for e in a.get("entities") or []:
            if e.get("symbol") in (ticker, None) and e.get("sentiment_score") is not None \
                    and (e.get("match_score") or 0.0) >= MATCH_SCORE_FLOOR:
                m = e.get("match_score") or 1.0
                wsum += m * e["sentiment_score"]
                w += m
                n += 1
    return (wsum / w if w else None), n


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None


def _stockdata_all(params, ticker=""):
    """Call /news/all with `params` (everything after the path, incl '?'), failing
    over across all configured StockData keys via keys.call_with_failover.

    Returns (http_status, api_error_code_or_None, articles_list). On AllKeysFailed
    returns (None, 'all_keys_failed', []); the caller tags the source
    ('stockdata_all_keys_failed') and falls back rather than returning blank."""
    from keys import AllKeysFailed, call_with_failover, stockdata_keys
    keys = stockdata_keys()
    if not keys:
        return None, "no_key_configured", []

    def req(k):
        url = ("https://api.stockdata.org/v1/news/all"
               + params + f"&api_token={k}")
        return requests.get(url, headers=_UA, timeout=10)

    try:
        r, _ = call_with_failover(keys, req, "news/all", ticker=ticker)
    except AllKeysFailed:
        return None, "all_keys_failed", []
    except RuntimeError:
        return None, "http_error", []
    return 200, None, (r.json() or {}).get("data") or []


def stockdata_windows(ticker, now=None, count=NEWS_NEWSCOUNT):
    """Primary sentiment: three rolling 24h windows ending at run time T (UTC).

      day1 = (T-72h, T-48h]  day2 = (T-48h, T-24h]  day3 = (T-24h, T]

    API note: /news/all only accepts DAY-granularity published_after /
    published_before (any time-bearing form is rejected with HTTP 400), but every
    article carries a precise published_at, so we fetch the recent window with a
    coarse day bound and bucket entities into the three 24h windows CLIENT-SIDE
    by published_at. Day3 ends at run time T, so it is a partial 24h window on
    the first scan of a day; all windows are fixed-length relative to T, NOT
    calendar days. n=0 windows carry tone 0.0 and fire no flag. No windows beyond
    these, no coverage floor, no thresholds.

    Returns: tone_1/2/3, n_scored_1/2/3, d12=tone2-tone1, d23=tone3-tone2,
    accelerating_up (d23>d12 and d23>0), accelerating_down (d23<d12 and d23<0),
    source ('stockdata' if a <=72h article exists in any window, even if all
    entities were match_score-filtered -> ambiguity not failure; 'vader' only on
    call failure or zero articles in all three windows), and run time T."""
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    base = {"tone_1": 0.0, "tone_2": 0.0, "tone_3": 0.0,
            "n_scored_1": 0, "n_scored_2": 0, "n_scored_3": 0,
            "d12": 0.0, "d23": 0.0,
            "accelerating_up": False, "accelerating_down": False,
            "source": "vader",
            # diagnostics (heartbeat; source decision above is unchanged)
            "routing": "error", "http_status": None, "api_error": None,
            "articles_returned": 0, "articles_in_windows": 0,
            "articles_per_window": {"day1": 0, "day2": 0, "day3": 0},
            "T": now.isoformat()}
    if not STOCKDATA_API_KEY:
        base["routing"] = "error"          # no key configured
        return base
    after = (now - _dt.timedelta(days=4)).strftime("%Y-%m-%d")  # coarse day bound
    # Do NOT "fix" published_after by passing a timestamp: /news/all rejects any
    # time-bearing form with HTTP 400 and only accepts whole-day YYYY-MM-DD. We
    # fetch a coarse 4-day span and bucket client-side by published_at below.
    params = (f"?symbols={ticker}&filter_entities=true&language=en"
              f"&published_after={after}&limit={count}")
    status, ecode, arts = _stockdata_all(params, ticker=ticker)
    base["http_status"] = status
    base["api_error"] = ecode
    if status != 200:                        # failed / all keys exhausted -> fall back
        base["routing"] = "vader_fallback"
        base["failure_tag"] = ("stockdata_all_keys_failed" if ecode == "all_keys_failed"
                               else None)
        return base
    base["articles_returned"] = len(arts)
    H = 3600.0
    wins = {"day1": [], "day2": [], "day3": []}
    per_win = {"day1": 0, "day2": 0, "day3": 0}
    has_any_win = False
    for a in arts:
        dt = _parse_ts(a.get("published_at"))
        if dt is None:
            continue
        age = (now - dt).total_seconds()
        if not 0 < age <= 72 * H:
            continue
        has_any_win = True                  # recent article exists in a window
        w = "day3" if age <= 24 * H else ("day2" if age <= 48 * H else "day1")
        per_win[w] += 1
        base["articles_in_windows"] += 1
        for e in a.get("entities") or []:
            if e.get("symbol") in (ticker, None) and e.get("sentiment_score") is not None \
                    and (e.get("match_score") or 0.0) >= MATCH_SCORE_FLOOR:
                m = e.get("match_score") or 1.0
                wins[w].append((m, float(e["sentiment_score"])))

    def tone(lst):
        return sum(m * s for m, s in lst) / sum(m for m, s in lst) if lst else 0.0

    n1, n2, n3 = len(wins["day1"]), len(wins["day2"]), len(wins["day3"])
    t1, t2, t3 = tone(wins["day1"]), tone(wins["day2"]), tone(wins["day3"])
    d12, d23 = t2 - t1, t3 - t2
    base.update({
        "tone_1": t1, "tone_2": t2, "tone_3": t3,
        "n_scored_1": n1, "n_scored_2": n2, "n_scored_3": n3,
        "d12": d12, "d23": d23,
        "accelerating_up": bool(d23 > d12 and d23 > 0),
        "accelerating_down": bool(d23 < d12 and d23 < 0),
        "articles_per_window": per_win,
    })
    # Routing (diagnostic only; source decision unchanged): StockData is the
    # source whenever recent (<=72h) articles exist in a window, even if all
    # entities were match_score-filtered (tone 0) - ambiguity, not failure.
    # 200-but-zero-in-windows = stockdata_empty; call failure = vader_fallback.
    base["routing"] = "stockdata" if has_any_win else "stockdata_empty"
    base["source"] = "stockdata" if has_any_win else "vader"
    return base


def sentiment_change(ticker, now=None, days=7):
    """Sentiment change gate inputs for one ticker.

    Pulls `days` of articles (StockData primary, match_score-weighted). tone_recent
    = weighted mean of sentiment over (T-72h, T]; tone_prior = weighted mean over
    (T-144h, T-72h]. delta = recent - prior. If StockData yields no scored article
    -> falls back to VADER (plain mean) on yfinance news. The scorer is NEVER
    mixed within a ticker. Flags: sentiment_up = delta >= +SD,
    sentiment_down = delta <= -SD, else none.

    Returns {tone_recent, tone_prior, delta, sentiment_up, sentiment_down,
             source, n_recent, n_prior}."""
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    H = 3600.0
    after = (now - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
    params = (f"?symbols={ticker}&filter_entities=true&language=en"
              f"&published_after={after}&limit=100")
    status, _ecode, arts = _stockdata_all(params, ticker=ticker)
    scored = []
    if status == 200:
        for a in arts:
            dt = _parse_ts(a.get("published_at"))
            if dt is None:
                continue
            age = (now - dt).total_seconds()
            for e in a.get("entities") or []:
                if e.get("symbol") in (ticker, None) and e.get("sentiment_score") is not None:
                    scored.append((age, float(e["sentiment_score"]),
                                   float(e.get("match_score") or 1.0)))
    source = "stockdata" if scored else "vader"
    if not scored:                        # VADER plain-mean fallback
        for _pt, title in (_fetch_yahoo(ticker, 100) or []):
            try:
                dt = _dt.datetime.fromtimestamp(int(_pt), tz=_dt.timezone.utc)
            except Exception:
                continue
            scored.append(((now - dt).total_seconds(), sen.score(title), 1.0))

    def wmean(lo, hi):
        items = [(s * m, m) for (a, s, m) in scored if lo < a <= hi]
        if not items:
            return 0.0, 0
        num = sum(n for n, _ in items)
        den = sum(m for _, m in items)
        return (num / den if den else 0.0), len(items)

    tone_recent, n_recent = wmean(0, 72 * H)       # (T-72h, T]
    tone_prior, n_prior = wmean(72 * H, 144 * H)   # (T-144h, T-72h]
    delta = tone_recent - tone_prior
    # Use the SD matching the scorer, so the threshold is scorer-consistent and
    # we never apply a StockData-tuned SD to a VADER score or vice-versa.
    sd = SENTIMENT_SD_STOCKDATA if source == "stockdata" else SENTIMENT_SD_VADER
    return {"tone_recent": round(tone_recent, 4), "tone_prior": round(tone_prior, 4),
            "delta": round(delta, 4),
            "sentiment_up": bool(delta >= sd),
            "sentiment_down": bool(delta <= -sd),
            "source": source, "n_recent": n_recent, "n_prior": n_prior}


def _fetch_yahoo(ticker, count):
    url = (f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}"
           f"&newsCount={count}&quotesCount=0")
    try:
        r = requests.get(url, headers=_UA, timeout=12)
        if r.status_code != 200:
            return []
        items = r.json().get("news") or []
    except Exception:
        return []
    out = []
    for it in items:
        if it.get("type") != "STORY":
            continue
        title = it.get("title")
        pt = it.get("providerPublishTime")
        if title and pt:
            out.append((pt, title))
    return out


def fetch_headlines(ticker, count=NEWS_NEWSCOUNT):
    """stockdata.org first, Yahoo fallback; returns cleaned (epoch, title) list."""
    days = NEWS_DAYS_BOOTSTRAP + 1
    raw = _fetch_stockdata(ticker)
    if not raw:
        raw = _fetch_yahoo(ticker, count)
    return _clean(raw)


def compute(ticker, bootstrap, prev_current=None, prev_change=None,
            floor=0.2):
    """Return (current10, change, significant, had_news).

    significant == True when |today's change| > |previous day's change| and the
    move is at least `floor` on the 0-10 scale (a real, accelerating move).
    """
    if bootstrap:
        cutoff = datetime.datetime.now() - datetime.timedelta(
            days=NEWS_DAYS_BOOTSTRAP)
    else:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=1)

    buckets = {}
    for pt, title in fetch_headlines(ticker):
        try:
            d = datetime.datetime.fromtimestamp(pt)
        except (OSError, ValueError, OverflowError):
            continue
        if d < cutoff:
            continue
        buckets.setdefault(d.strftime("%Y-%m-%d"), []).append(sen.score(title))

    if not buckets:
        return prev_current, None, False, False

    daily = [(day, round((sum(v) / len(v) + 1.0) * 5.0, 2))
             for day, v in sorted(buckets.items())]  # ascending by day
    current = daily[-1][1]
    # day-of-month-diff used to detect gaps (non-consecutive news days)
    days = [datetime.datetime.strptime(d, "%Y-%m-%d") for d, _ in daily]

    if prev_current is not None:
        change = round(current - prev_current, 2)
        gap = (days[-1] - days[0]).days + 1 > len(days)  # any gap across window
        if prev_change is None or gap:
            # no reliable baseline (missing stored change, or a news gap) ->
            # any move is significant
            sig = change != 0
        else:
            sig = (abs(change) >= floor and abs(change) > abs(prev_change))
        return current, change, sig, True

    # bootstrap: compare last change to the change before it
    if len(daily) >= 2:
        change = round(daily[-1][1] - daily[-2][1], 2)
        gap = (days[-1] - days[-2]).days > 1
        if len(daily) >= 3 and not gap:
            prior = round(daily[-2][1] - daily[-3][1], 2)
            sig = abs(change) >= floor and abs(change) > abs(prior)
        else:
            # sparse (2 days) or gap in coverage -> can't establish a reliable
            # prior, so assume the last-day move is real
            sig = change != 0
        return current, change, sig, True
    return current, 0.0, False, True
