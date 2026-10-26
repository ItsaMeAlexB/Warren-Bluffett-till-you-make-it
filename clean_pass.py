#!/usr/bin/env python3
"""pass.py - ONE clean pass over Portfolio.xlsx per the sheet spec.

Reads thresholds at runtime from the sheet's "Absolute Thresholds" block (rows
4-9): Sentiment Change (Predetermined) = StockData sentiment SD, Sentiment
Change (VADER) = VADER sentiment SD, Z-Score, Positivity, Fundamentals.

Per row it resolves a working ticker per source (with write-back into the empty
ticker columns), fills Company Description (shortest 1-sentence) and Recent News
(full headline with greatest |weighted_score| over the last 72h), computes the
sentiment-change gate and value signals, writes results back in place WITHOUT
touching the sheet layout, then prints the report and stops.

The StockData article-level SD is recomputed over the pooled corpus in
articles.csv once n>=100 and written back to the 'Sentiment Change
(Predetermined)' cell (never to code). SENTIMENT_SD_VADER falls back to config.

Usage:
  python3 pass.py --dry-run   # compute + report, do not save
  python3 pass.py --write     # the single real pass (saves)
"""
import csv
import datetime
import hashlib
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import openpyxl

import config as C
import cache
import cascade
import eulerpool_fund
import market
import news
import quarterly as Q
import scores
import sentiment as sen
from calculations import compute_zscore

# 1-based openpyxl column of each field in the collapsed single-Ticker layout
# (header row 15): the three per-source ticker columns were collapsed into ONE
# 'Ticker' column at column 1, so every subsequent column shifted left by 2.
J = {"ticker": 1, "company": 2, "country": 3, "industry": 4,
     "desc": 5, "news": 6, "sent": 7, "z": 8, "pos": 9, "fund": 10,
     "bm": 11, "shares": 12, "avg_cost": 13, "target": 14, "price": 15,
     "value_held": 16, "profit": 17, "currency": 18,
     "prev_shares": 19, "prev_price": 20, "action": 21, "notes": 22}

ARTICLES = os.path.join(C.HERE, "articles.csv")
H = 3600.0

_SENT_LABELS = {
    "Sentiment Change (Predetermined)".lower(): "sd_sd",
    "Sentiment Change (VADER)".lower(): "vader_sd",
}
_GATE_LABELS = {"z-score": "z", "z score": "z", "z price": "z",
                "positivity": "pos", "fundamentals": "fund"}


def _headline_hash(title):
    n = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    return hashlib.md5(n.encode()).hexdigest()


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None


def read_thresholds(ws, hdr_row):
    """Read the Absolute Thresholds block (rows above the header) into a dict.

    Returns keys: sd_sd, vader_sd, z, pos, fund. Missing -> config defaults.
    """
    th = {"sd_sd": C.SENTIMENT_SD_STOCKDATA, "vader_sd": C.SENTIMENT_SD_VADER,
          "z": C.default_thresholds()["z"], "pos": C.default_thresholds()["positivity"],
          "fund": C.default_thresholds()["fin_health"]}
    for row in ws.iter_rows(min_row=1, max_row=hdr_row - 1, max_col=3,
                            values_only=True):
        key = row[0]
        if not isinstance(key, str):
            continue
        k = key.strip().lower()
        if k in _SENT_LABELS and row[1] is not None:
            th[_SENT_LABELS[k]] = float(row[1])
        elif k in _GATE_LABELS and row[1] is not None:
            th[_GATE_LABELS[k]] = float(row[1])
    return th


def find_header_row(ws):
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, J["company"]).value == "Company name":
            return r
    return None


def _first(*toks):
    for t in toks:
        if t:
            return str(t).strip()
    return None


def company_description(info, euler, company=None):
    """yfinance `longBusinessSummary` first sentence (spec 2); only fall back to
    an eulerpool description if that is empty or identical to the company name.
    Never returns the company name itself."""
    if info:
        s = (info.get("longBusinessSummary") or "").strip()
        if s:
            first = s.split(".", 1)[0].strip() + "."
            if first.strip().lower() != str(company or "").strip().lower():
                return first
    if euler and isinstance(euler.get("kpi"), dict):
        for key in ("description", "business", "shortDescription", "summary"):
            v = euler["kpi"].get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def cache_window(ticker, now):
    """Rolling-cache fetch window (days) for a ticker from articles.csv
    (cache.py): no rows -> 7, last_fetch==today -> 1, else today-last+1."""
    rows = []
    if os.path.exists(ARTICLES):
        try:
            rows = list(csv.DictReader(open(ARTICLES)))
        except Exception:
            rows = []
    last = cache.per_ticker_last(rows).get(ticker)
    has = any(r.get("ticker") == ticker for r in rows)
    return cache.fetch_window(has, last, now.date().isoformat())


def pull_stockdata(ticker, now, days=7):
    """One StockData pull for a ticker -> (http_status, articles list).
    `days` is the rolling-cache fetch window (published_after = now - days)."""
    after = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    params = (f"?symbols={ticker}&filter_entities=true&language=en"
              f"&published_after={after}&limit=100")
    try:
        status, ecode, arts = news._stockdata_all(params, ticker=ticker)
    except Exception:
        return None, []            # network error -> caller falls back
    if status != 200:
        return status, []
    return status, arts


def score_pull(ticker, arts, now):
    """Score a StockData pull into windows; returns (recent, n, prior, n, headline).

    recent/prior = match_score-weighted mean sentiment over (T-72h,T] and
    (T-144h,T-72h]. headline = full title of the article whose entity has the
    greatest |sentiment_score*match_score| within 72h (full text, untrimmed).
    Empty -> (None, None, None, None, None) so caller falls back to VADER.
    """
    rows = []                     # (age, sent, match, title)
    for a in arts:
        dt = _parse_dt(a.get("published_at"))
        if dt is None:
            continue
        age = (now - dt).total_seconds()
        for e in a.get("entities") or []:
            if e.get("symbol") in (ticker, None) and e.get("sentiment_score") is not None:
                rows.append((age, float(e["sentiment_score"]),
                             float(e.get("match_score") or 1.0), a.get("title") or ""))
    if not rows:
        return None, 0, None, 0, None

    def wmean(lo, hi):
        items = [(s, m) for (a_, s, m, _t) in rows if lo < a_ <= hi]
        if not items:
            return None, 0
        return sum(s * m for s, m in items) / sum(m for _, m in items), len(items)

    recent, n_r = wmean(0, 72 * H)
    prior, n_p = wmean(72 * H, 144 * H)
    best = None
    for (a_, s, m, t) in rows:
        if not (0 < a_ <= 72 * H):
            continue
        w = s * m
        if best is None or abs(w) > abs(best[0]):
            best = (w, t, a_ / (24 * H))
    headline = best[1] if best else None
    head_age = round(best[2], 1) if best else None
    return (recent if recent is not None else 0.0, n_r,
            prior if prior is not None else 0.0, n_p, headline, head_age)


def vader_fallback(ticker, now):
    """Yahoo news headlines + VADER -> (recent, n, prior, n, headline)."""
    items = news._fetch_yahoo(ticker, 100) or []
    rows, head_best = [], None
    for pt, title in items:
        try:
            dt = datetime.datetime.fromtimestamp(int(pt), tz=datetime.timezone.utc)
        except Exception:
            continue
        age = (now - dt).total_seconds()
        s = sen.score(title)
        rows.append((age, s))
        if 0 < age <= 72 * H and (head_best is None or abs(s) > abs(head_best[0])):
            head_best = (s, title, age)
    if not rows:
        return None, 0, None, 0, None, None

    def mean(lo, hi):
        v = [s for (a_, s) in rows if lo < a_ <= hi]
        return (sum(v) / len(v), len(v)) if v else (0.0, 0)

    r, n_r = mean(0, 72 * H)
    p, n_p = mean(72 * H, 144 * H)
    return r, n_r, p, n_p, (head_best[1] if head_best else None), \
        (round(head_best[2] / (24 * H), 1) if head_best else None)


def append_articles(sd_rows, vader_rows):
    """Merge scored-article rows into articles.csv via the rolling cache
    (cache.py): dedup by headline_hash, prepend new rows, prune to 144h, save."""
    rows = []
    if os.path.exists(ARTICLES):
        try:
            for r in csv.DictReader(open(ARTICLES)):
                rows.append({k: r.get(k, "") for k in cache.FIELDS})
        except Exception:
            rows = []
    today = datetime.date.today().isoformat()
    new = []
    for r in sd_rows + vader_rows:
        new.append({
            "ticker": r.get("ticker", ""),
            "headline": r.get("headline", ""),
            "sentiment": r.get("sentiment", r.get("sentiment_score", "")),
            "source": r.get("source", "stockdata"),
            "published_at": r.get("published_at", ""),
            "fetch_date": today,
            "headline_hash": r.get("headline_hash", ""),
        })
    return cache.merge_and_save(ARTICLES, rows, new, today)


def compute_sd(ws, hdr_row):
    """Pooled StockData article SD over articles.csv (no re-fetch). Computes from
    whatever is present whenever n>=2; writes to the 'Sentiment Change
    (Predetermined)' cell. Returns (sd_or_None, n, reason). reason tags n<2."""
    vals = []
    if os.path.exists(ARTICLES):
        for r in csv.DictReader(open(ARTICLES)):
            if r.get("source") == "stockdata":
                v = r.get("sentiment") if r.get("sentiment") not in (None, "") \
                    else r.get("sentiment_score")
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
    n = len(vals)
    if n < 2:
        return None, n, "n<2 insufficient"
    mean = statistics.fmean(vals)
    var = statistics.pvariance(vals)
    sd = round(statistics.pstdev(vals), 4)
    # step-by-step (spec A.1)
    print("[SD] source=stockdata rows n=%d" % n)
    print("[SD] first 5 values: %s" % vals[:5])
    print("[SD] mean      = %.6f" % mean)
    print("[SD] variance  = %.6f" % var)
    print("[SD] SD        = %.4f" % sd)
    for row in range(1, hdr_row):
        if str(ws.cell(row, 1).value or "").strip().lower() == \
                "Sentiment Change (Predetermined)".lower():
            ws.cell(row, 2, sd)
            return sd, n, "ok"
    return sd, n, "ok"


def bulk_fetch_stockdata(tickers, now, batch=15, max_pages=3):
    """Bulk StockData fetch (spec B): multi-symbol batching + pagination, only
    for tickers MISSING stockdata rows in articles.csv (never re-fetch cached).
    Returns (info, new_rows) where info = {calls, covered, missing, exhausted}."""
    cached = set()
    if os.path.exists(ARTICLES):
        for r in csv.DictReader(open(ARTICLES)):
            if r["source"] == "stockdata":
                cached.add(r["ticker"])
    missing = [t for t in dict.fromkeys(tickers) if t not in cached]
    info = {"calls": 0, "covered": 0, "missing": len(missing), "exhausted": False}
    new_rows, seen = [], set()
    if not missing:
        return info, new_rows
    after = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    for i in range(0, len(missing), batch):
        chunk = missing[i:i + batch]
        syms = ",".join(chunk)
        for page in range(1, max_pages + 1):
            info["calls"] += 1
            params = (f"?symbols={syms}&filter_entities=true&language=en"
                      f"&published_after={after}&limit=50&page={page}")
            try:
                status, ecode, arts = news._stockdata_all(params, ticker=syms)
            except Exception:
                info["exhausted"] = True      # network error -> stop
                break
            if status != 200:
                if status in (401, 402, 403, 429) or ecode == "all_keys_failed":
                    info["exhausted"] = True
                break
            if not arts:
                break
            info["covered"] += 1
            for a in arts:
                hh = _headline_hash(a.get("title"))
                if hh in seen:
                    continue
                seen.add(hh)
                for e in a.get("entities") or []:
                    sym = e.get("symbol")
                    if sym in chunk and e.get("sentiment_score") is not None:
                        new_rows.append({"ticker": sym,
                                         "published_at": a.get("published_at"),
                                         "source": "stockdata",
                                         "sentiment_score": float(e["sentiment_score"]),
                                         "match_score": float(e.get("match_score") or 0.0),
                                         "headline_hash": hh})
    return info, new_rows


def interchangeability_test(sample, now):
    """Spec C: try StockData / Eulerpool / yfinance with the SAME ticker each.
    Returns [(ticker, stockdata_ok, euler_ok, yf_ok)]."""
    out = []
    for tk in sample:
        status, _ = pull_stockdata(tk, now)
        sd_ok = status == 200
        eok = False
        try:
            eok = eulerpool_fund.fetch(tk) is not None
        except Exception:
            eok = False
        yok = False
        try:
            info, _, _ = market.fetch(tk)
            yok = bool(info and (info.get("shortName") or info.get("longName")))
        except Exception:
            yok = False
        out.append((tk, sd_ok, eok, yok))
    return out


def main():
    write = "--write" in sys.argv
    wb = openpyxl.load_workbook(C.XLSX_PATH)
    ws = wb["Sheet1"]
    hdr = find_header_row(ws)
    if hdr is None:
        sys.exit("header row not found")
    th = read_thresholds(ws, hdr)

    now = datetime.datetime.now(datetime.timezone.utc)
    ticker_src = {}
    sent_src = {"stockdata": 0, "vader": 0}
    gates = {"buy": 0, "sell": 0, "none": 0}
    value_pass = marked = no_history = 0
    desc_fill = news_fill = rows_processed = 0
    cell_writes = []
    no_hist_tickers = []
    sd_bucket, vader_bucket = [], []

    # ---- bulk StockData fetch (spec B): only tickers MISSING stockdata rows
    ticks_all = [str(ws.cell(r, J["ticker"]).value)
                 for r in range(hdr + 1, ws.max_row + 1)
                 if ws.cell(r, J["ticker"]).value]
    bi, bnew = bulk_fetch_stockdata(ticks_all, now)
    if write:
        append_articles(bnew, [])

    # ---- Eulerpool quotes batch (spec §1): ONE call for all tickers ----
    qb = {}
    try:
        qb = eulerpool_fund.quotes_batch(ticks_all)
    except Exception:
        qb = {}
    if not qb and ticks_all:
        # schema mismatch / empty parse -> never silently rely on a 0-row batch
        print("[WARN] quotes_batch returned 0 rows for %d tickers -> "
              "falling back to per-ticker prices this run" % len(ticks_all))
    else:
        print(f"[quotes_batch] parsed {len(qb)}/{len(ticks_all)} tickers in 1 call")

    # ---- interchangeability test (spec C): 10 tickers spanning US/EU/UK/HK
    sample = ["ASML", "BEAM", "RHHBY", "SAAB-B.ST", "ACT.DE", "2171.HK",
              "CS.PA", "KOG.OL", "BCVN.SW", "SZG.DE"]
    ix = interchangeability_test(sample, now)

    # ---- report counters (D/E/F)
    fb = {"price_date": 0, "news_age": 0, "fallback_failed": 0}
    no_news_tag = 0
    n_unresolved = 0
    desc_euler_fetches = 0
    used_dist = {}

    for r in range(hdr + 1, ws.max_row + 1):
        tk_t = ws.cell(r, J["ticker"]).value
        company = ws.cell(r, J["company"]).value
        if not (tk_t or company):
            continue
        rows_processed += 1
        tk = str(tk_t).strip() if tk_t else None
        cells = {"ticker": tk, "company": company}
        row_tags = []
        sd_fail_reason = None

        def _wb(sym):                  # write-back a name/ISIN-resolved ticker
            if write:
                ws.cell(r, J["ticker"], sym)
            cell_writes.append(("ticker", sym))

        # ---- yfinance via cascade (single Ticker cell; name fallback) ----
        def _yf_fetch(t):
            try:
                return market.fetch(t)
            except Exception:
                return None
        yres, yinfo = cascade.resolve(cells, "yfinance", _yf_fetch, row_tags, _wb)
        if yres is None:
            info, price_df, statements = {}, None, (None, None, None)
            main_w = None
        else:
            info, price_df, statements = yres
            main_w = yinfo.get("ticker") or tk

        # ---- eulerpool: same single Ticker; only queried lazily in fallbacks ----
        eu_w = tk
        euler = None

        # ---- stockdata news via cascade (VADER fallback below) ----
        def _sd_fetch(t):
            nonlocal sd_fail_reason
            status, arts = pull_stockdata(t, now, cache_window(t, now))
            if status == 200 and arts:
                return (status, arts)
            sd_fail_reason = ("quota" if status in (401, 402, 403, 429)
                              else ("call_failed" if status is None else "no_articles"))
            return None
        sdres, sdinfo = cascade.resolve(cells, "stockdata", _sd_fetch, row_tags, _wb)
        if sdres is not None:
            _sstatus, sarts = sdres
            sd_w = sdinfo.get("ticker") or tk or main_w
        else:
            _sstatus, sarts = (None, [])
            sd_w = tk or main_w

        if tk:
            ticker_src["ticker"] = ticker_src.get("ticker", 0) + 1

        # ---- company description (yfinance primary, eulerpool lazy fallback)
        desc = company_description(info, None, company)
        if desc is None and eu_w:
            try:
                euler = eulerpool_fund.fetch(C.EULER_TICKER_MAP.get(eu_w, eu_w))
                desc_euler_fetches += 1
            except Exception:
                euler = None
            if euler is not None:
                desc = company_description(info, euler, company)
        if desc:
            desc_fill += 1
            if write:
                ws.cell(r, J["desc"], desc)
            cell_writes.append(("company description", desc[:40]))

        # ---- recent news + sentiment (StockData primary, VADER fallback)
        sd = th["vader_sd"]
        status, arts = _sstatus, sarts      # articles already pulled via cascade
        rr = pr = None
        if status == 200 and arts:
            rr, nr, pr, np_, headline, head_age = score_pull(sd_w or main_w, arts, now)
            if rr is not None:
                sent_src["stockdata"] += 1
                sd = th["sd_sd"]
                for a in arts:
                    dt = _parse_dt(a.get("published_at"))
                    if dt is None:
                        continue
                    for e in a.get("entities") or []:
                        if e.get("symbol") in (sd_w or main_w, None) and \
                                e.get("sentiment_score") is not None:
                            sd_bucket.append({
                                "ticker": main_w,
                                "published_at": a.get("published_at"),
                                "source": "stockdata",
                                "headline": a.get("title") or "",
                                "sentiment_score": float(e["sentiment_score"]),
                                "match_score": float(e.get("match_score") or 0.0),
                                "headline_hash": _headline_hash(a.get("title"))})
            else:
                rr = pr = None
        if rr is None:                              # VADER fallback
            rr, nr, pr, np_, headline, head_age = vader_fallback(main_w, now)
            if rr is not None:
                sent_src["vader"] += 1
                # spec §3: unconditional tag whenever VADER fires (not cascade-dependent)
                row_tags.append("sentiment=vader; reason=" + (sd_fail_reason or "no_articles"))
                # step 3 (visible): stockdata -> yfinance news fallback
                row_tags.append("source_alt=yfinance reason=stockdata_unavailable")
                sd = th["vader_sd"]
                for _pt, _t in (news._fetch_yahoo(main_w, 100) or []):
                    vader_bucket.append({
                        "ticker": main_w, "published_at": _pt, "source": "vader",
                        "sentiment_score": sen.score(_t), "match_score": "",
                        "headline": _t,
                        "headline_hash": _headline_hash(_t)})
        if rr is None:
            rr, pr, delta = 0.0, 0.0, 0.0
            nr = np_ = 0
            headline = None
            head_age = None
        else:
            delta = rr - pr
        if nr == 0 and np_ == 0:
            no_history += 1
            no_hist_tickers.append(main_w)
        if headline:
            news_fill += 1
            if write:
                ws.cell(r, J["news"], headline)          # full, untrimmed
            cell_writes.append(("recent news", headline[:40]))

        # ---- write sentiment change (delta)
        if write:
            ws.cell(r, J["sent"], round(delta, 4))
        cell_writes.append(("sentiment change", round(delta, 4)))

        # ---- z / positivity / fundamentals
        z = None
        if price_df is not None and not price_df.empty:
            z, _ = compute_zscore(price_df)
        pos = scores.positivity(price_df)[0] if price_df is not None else 0.5
        kbv = (euler or {}).get("kpi", {}).get("kbv")
        ptb = info.get("priceToBook")
        btm = (1.0 / kbv) if (kbv and kbv > 0) else \
            ((1.0 / ptb) if (ptb and ptb > 0) else None)
        # ---- financial waterfall (spec 1): yfinance quarterly > eulerpool
        #      quarterly > yfinance annual > eulerpool annual > exclude.
        #      Notes ONLY for annual_fallback / no_financials -> clean notes.
        wf = Q.financial_waterfall(main_w, eu_w, info, statements, btm, euler)
        fin = wf["score"]
        used_dist[wf["used"]] = used_dist.get(wf["used"], 0) + 1
        fund_tags = list(wf["notes"])        # annual_fallback / no_financials
        if wf.get("gapped"):
            fund_tags.append("delta_source=annual fallback reason=gapped_quarters")
        if not wf["ok"]:
            fin = None                       # no_financials -> excluded from scoring


        # ---- gates + value from SHEET thresholds
        buy_enabled = bool(z is not None and z <= -th["z"] and delta >= sd)
        sell_enabled = bool(z is not None and z >= th["z"] and delta <= -sd)
        value_pass_row = bool(fin is not None and fin >= th["fund"]
                              and pos >= th["pos"])
        if buy_enabled and sell_enabled:
            result = "CONTRADICTION"
        elif buy_enabled and not sell_enabled and value_pass_row:
            result = "BUY/HOLD"
        elif (not buy_enabled) and sell_enabled:
            result = "SELL"
        elif buy_enabled and not sell_enabled:
            result = "GATE_OPEN"
        else:
            result = "NONE"
        gates["buy" if buy_enabled else ("sell" if sell_enabled else "none")] += 1
        if value_pass_row:
            value_pass += 1
        if result in ("BUY/HOLD", "SELL"):
            marked += 1

        # ---- fallback tags (spec E) + notes column
        tags = list(fund_tags) + list(row_tags)
        if "source_unresolved" in row_tags:
            n_unresolved += 1
        if price_df is not None and not price_df.empty:
            last = price_df.index[-1]
            pd_ = last.date().isoformat()
            if (now.date() - last.date()).days > 2:
                tags.append("price_date=" + pd_)
                fb["price_date"] += 1
        if head_age is not None and head_age > 3:
            tags.append("news_age_days=%s" % head_age)
            fb["news_age"] += 1
        if rr is None and nr == 0 and np_ == 0 and not headline:
            tags.append("source=fallback_failed")
            fb["fallback_failed"] += 1
        if not headline:
            no_news_tag += 1
        prev_notes = ws.cell(r, J["notes"]).value
        if tags:
            if prev_notes:
                notes = str(prev_notes).rstrip("; ") + "; " + "; ".join(tags)
            else:
                notes = "; ".join(tags)
        else:
            notes = prev_notes

        # ---- write back z / pos / fund / btm / action / notes
        if write:
            ws.cell(r, J["z"], round(z, 4) if z is not None else None)
            ws.cell(r, J["pos"], round(pos, 4))
            ws.cell(r, J["fund"], round(fin, 4) if fin is not None else None)
            ws.cell(r, J["bm"], round(btm, 4) if btm is not None else None)
            ws.cell(r, J["action"], result)
            if tags:
                ws.cell(r, J["notes"], notes)

    # ---- persist the SD corpus and recompute the StockData SD (spec A)
    n_new = append_articles(sd_bucket, vader_bucket) if write else 0
    sd_val, sd_n, sd_reason = compute_sd(ws, hdr)

    if write:
        wb.save(C.XLSX_PATH)

    # ============================ REPORT ====================================
    ok_ix = [t for (t, s, e, y) in ix if s and e and y]
    print("=== PASS REPORT ===")
    print(f"rows processed: {rows_processed}")
    # A: SD
    print(f"[A] StockData SD: n={sd_n} sd={sd_val} reason={sd_reason}")
    # B: bulk fetch
    print(f"[B] bulk fetch: calls={bi['calls']} covered_batches={bi['covered']} "
          f"missing_tickers={bi['missing']} exhausted={bi['exhausted']} "
          f"new_stockdata_rows={len(bnew)}")
    print(f"[B2] eulerpool quotes batch: {len(qb)}/{len(ticks_all)} tickers in 1 call "
          f"(KPI/quarterly still per-ticker -> quota cost)")
    print(f"[B3] cascade: unresolved_rows={n_unresolved}  vader_rows={sent_src['vader']} "
          f"(VADER rows carry note 'sentiment=vader; reason=...')")
    eul_q = used_dist.get("eulerpool_quarterly", 0)
    eul_a = used_dist.get("eulerpool_annual", 0)
    euler_calls_used = 3 * eul_q + eul_a + desc_euler_fetches
    print(f"[B4] eulerpool calls estimate = 3*{eul_q}(quarterly) + {eul_a}(annual) "
          f"+ {desc_euler_fetches}(desc fallback) = {euler_calls_used} for "
          f"{rows_processed} rows (≈ {round(euler_calls_used / max(rows_processed, 1), 1)}/row; "
          f"was ~4/row when eulerpool-first, quota ~100/day single key)")
    # C: interchangeability
    print("[C] ticker interchangeability (10 : US/EU/UK/HK):")
    for (t, s, e, y) in ix:
        print(f"     {t:9s} stockdata={s} eulerpool={e} yfinance={y}")
    print(f"     all-three-ok: {len(ok_ix)}/10 -> "
          f"{'single-Ticker collapse candidate' if len(ok_ix) >= 9 else 'keep 3 columns, write-back to all'}")
    print("ticker_source_used:", dict(ticker_src))
    print("cells written back categories: "
          + ", ".join(dict.fromkeys(c[0] for c in cell_writes)))
    print(f"sentiment_source dist: {sent_src}")
    print(f"gate dist (buy/sell/none): {gates['buy']}/{gates['sell']}/{gates['none']}")
    print(f"value-pass: {value_pass}   marked (BUY/HOLD+SELL): {marked}")
    print(f"Company Description fill: {desc_fill}/{rows_processed}")
    print(f"Recent News fill: {news_fill}/{rows_processed}  (no-headline rows: {no_news_tag})")
    # D: fundamentals waterfall used-distribution
    print(f"[D] fundamentals waterfall used: {dict(used_dist)} "
          f"(notes carry no tag for normal quarterly; only annual_fallback/no_financials)")
    # E: fallback tags
    print(f"[E] fallback tags: {fb}  (price_date/news_age/source=fallback_failed)")
    print(f"articles appended (per-row): {n_new}   StockData SD reason: {sd_reason}")
    print(f"insufficient article history: {no_history} -> {no_hist_tickers}"
          if no_hist_tickers else "insufficient article history: 0")
    print(f"[{'WROTE' if write else 'DRY-RUN, no save'}] thresholds used: "
          f"sd_sd={th['sd_sd']} vader_sd={th['vader_sd']} z={th['z']} "
          f"pos={th['pos']} fund={th['fund']}")


if __name__ == "__main__":
    main()
