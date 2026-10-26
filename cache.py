#!/usr/bin/env python3
"""cache.py - articles.csv rolling cache (final-pass spec A).

Single file, no sidecar. Newline header:
    ticker, headline, sentiment, source, published_at, fetch_date, headline_hash

On each run:
  1. prune rows with fetch_date < today - 6 days (144h rolling window)
  2. per ticker: last_fetch = max(fetch_date) ->
         no rows                   -> fetch 7 days
         last_fetch == today       -> fetch 1 day
         else                      -> fetch today - last_fetch + 1 days
  3. fetch (caller), dedup by headline_hash, PREPEND new rows to the top
  4. save - one file, pruned to 144h

prune / per_ticker_last / fetch_window / merge_and_save are pure and
unit-testable (no network, no sheet).
"""
import csv
import datetime
import os

FIELDS = ["ticker", "headline", "sentiment", "source", "published_at",
          "fetch_date", "headline_hash"]
ROLLING_DAYS = 6          # prune to 144h
MAX_FETCH_DAYS = 7        # legacy fresh-fetch span when no cache


def _d(s):
    try:
        return datetime.date.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def prune(rows, today=None):
    """Keep only rows within the 144h rolling window (fetch_date >= today-6)."""
    today_d = _d(_today(today))
    keep, cutoff = [], None
    if today_d:
        cutoff = today_d - datetime.timedelta(days=ROLLING_DAYS)
    for r in rows:
        fd = _d(r.get("fetch_date"))
        if fd is None or cutoff is None or fd >= cutoff:
            keep.append(r)
    return keep


def per_ticker_last(rows):
    """{ticker: max(fetch_date)}."""
    last = {}
    for r in rows:
        t = r.get("ticker")
        fd = r.get("fetch_date") or ""
        if t and fd > last.get(t, ""):
            last[t] = fd
    return last


def fetch_window(has_rows, last_fetch, today=None):
    """Number of days to fetch for one ticker, from its last fetch date."""
    today_d = _d(_today(today))
    if not has_rows or not last_fetch:
        return MAX_FETCH_DAYS
    lf = _d(last_fetch)
    if lf is None or today_d is None:
        return MAX_FETCH_DAYS
    diff = (today_d - lf).days
    if diff <= 0:
        return 1
    return min(diff + 1, MAX_FETCH_DAYS)


def _today(today=None):
    if today:
        return today.isoformat() if hasattr(today, "isoformat") else str(today)
    return datetime.date.today().isoformat()


def merge_and_save(path, rows, new_rows, today=None):
    """Dedup by headline_hash, prepend new rows, prune to 144h, write file.

    rows     - current cache rows (dicts)
    new_rows - freshly fetched rows to add on top
    Returns the number of rows actually added (after dedup).
    """
    today_s = _today(today)
    seen = {r.get("headline_hash") for r in rows if r.get("headline_hash")}
    fresh = [r for r in new_rows
             if r.get("headline_hash") and r["headline_hash"] not in seen]
    merged = prune(fresh + rows, today_s)      # new on top, then prune
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in merged:
            w.writerow({k: r.get(k) for k in FIELDS})
    return len(fresh)


if __name__ == "__main__":
    # ---- pure unit test (spec A): fetch windows for 3 mock last_fetch dates
    today = "2026-10-26"
    def w(has, last): return fetch_window(has, last, today)
    assert w(False, None) == 7, w(False, None)            # no rows   -> 7
    assert w(True, "2026-10-26") == 1, w(True, "2026-10-26")  # today   -> 1
    assert w(True, "2026-10-25") == 2, w(True, "2026-10-25")  # yesterday-> 2
    assert w(True, "2026-10-23") == 4, w(True, "2026-10-23")  # 3d ago  -> 4
    n = fetch_window(True, "2026-10-20", today)           # 6d ago
    assert n == 7, n                                       # capped at 7
    print("Part A unit test PASS: fetch windows no-rows=7 today=1 yesterday=2 "
          "3d-ago=4 cap=7")

    # prune / per_ticker_last / merge sanity
    rows = [
        {"ticker": "A", "headline_hash": "h1", "fetch_date": today},
        {"ticker": "A", "headline_hash": "h2", "fetch_date": "2026-10-10"},  # stale
    ]
    pr = prune(rows, today)
    print("prune keeps %d of %d (stale 10-10 dropped)" % (len(pr), len(rows)))
    assert len(pr) == 1 and pr[0]["headline_hash"] == "h1"
    assert per_ticker_last(rows) == {"A": today}
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "articles.csv")
    added = merge_and_save(p, rows, [{"ticker": "A", "headline_hash": "h3",
                                      "fetch_date": today}], today)
    got = list(csv.DictReader(open(p)))
    print("merge_and_save added=%d total_rows=%d top_first=%s" %
          (added, len(got), got[0]["headline_hash"]))
    assert added == 1
    assert got[0]["headline_hash"] == "h3"       # prepended to top
    assert len(got) == 2                          # stale pruned, new kept
    print("Part A unit test PASS: prune + per_ticker_last + merge(prepend/dedup)")
