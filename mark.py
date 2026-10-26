#!/usr/bin/env python3
"""mark.py - Score & mark each name per the final rules (no training, no fitting).

Per name:
  - Sentiment change gate (news.sentiment_change): sentiment_up / sentiment_down.
  - Price z (21-day): price_high (z>=+1), price_low (z<=-1).
  - Financial score = S_unified (theta=1.0, lam=1.0, R&D/Capex gated), then size
    bump applied individually; threshold >=7 passes.
  - 6-month positivity (126-day up ratio), size bump applied individually; >=0.55.
  - Gates: sell_enabled = price_high AND sentiment_down; buy_enabled = price_low
    AND sentiment_up.
  - Value signals both must pass for BUY/HOLD; sell needs only the sell gate.

Marking table:
  buy_enabled, sell_enabled, both_value_pass -> result
  Yes,No,Yes -> BUY/HOLD ; No,Yes,* -> SELL ; Yes,No,No -> GATE_OPEN ; No,No -> NONE
  Yes,Yes -> CONTRADICTION (logged)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market
import scores
import eulerpool_fund
import news
import config as C
from calculations import compute_zscore, size_percentile, size_weight
from scorecard import s_unified
from sample_calibration import _components

FIN_SCORE_PASS = 7.0
POS_PASS = 0.55
Z_HI, Z_LO = 1.0, -1.0


def one(prev):
    ticker = prev["ticker"]
    out = {"ticker": ticker, "result": "NONE", "_err": None,
           "sentiment_source": "", "n_recent": 0, "n_prior": 0,
           "price_high": False, "price_low": False,
           "sentiment_up": False, "sentiment_down": False,
           "buy_enabled": False, "sell_enabled": False,
           "fin_score": None, "pos": None, "size_pct": None, "size_w": 1.0,
           "both_value": False}
    try:
        euler = eulerpool_fund.fetch(ticker)
        info, price_df, statements = market.fetch(ticker)
        comps = _components(info or {}, statements, euler)
        fh = scores.financial_health(info or {}, statements, None, C.default_thresholds(), euler)[0]
        comps["_fh"] = fh
        mcap = info.get("marketCap") if info else None
        out.update(comps)
        out["mcap"] = mcap
    except Exception as e:
        out["_err"] = str(e)
        return out
    try:
        s = news.sentiment_change(ticker)
        out.update({k: s[k] for k in
                    ("sentiment_up", "sentiment_down", "source", "n_recent", "n_prior")})
    except Exception as e:
        out["_err_s"] = str(e)
    # price z
    z = None
    if price_df is not None and not price_df.empty:
        z, _ = compute_zscore(price_df)
    out["z"] = z
    out["price_high"] = bool(z is not None and z >= Z_HI)
    out["price_low"] = bool(z is not None and z <= Z_LO)
    # positivity (6 months = 126 trading days)
    pos, _ = scores.positivity(price_df)
    out["pos"] = pos
    return out


def scan(prev_rows):
    # per-name sentiment/gates + raw fin/pos first (size needs all mcaps)
    rows = [one(p) for p in prev_rows]
    # universe size bump applied individually to fin score and positivity
    mcaps = [r.get("mcap") for r in rows]
    pct_map = size_percentile(mcaps) if any(m is not None for m in mcaps) else {}
    fin_rows = [{k: r.get(k) for k in
                 ("roa", "delta_roa", "cfo_assets", "accruals", "delta_leverage",
                  "delta_liquidity", "share_issuance", "delta_gross_margin",
                  "delta_aturn", "rd_intensity", "capex_intensity")} for r in rows]
    sc = s_unified(fin_rows)      # theta0=1.0, lam=1.0 (defaults)
    for i, r in enumerate(rows):
        r["fin_score"] = sc[i]["S_unified"]
        p = pct_map.get(i, None)
        r["size_pct"] = round(p, 1) if p is not None else None
        w = size_weight(p) if p is not None else 1.0
        r["size_w"] = round(w, 3)
        r["fin_score_w"] = r["fin_score"] * w
        r["pos_w"] = (r["pos"] or 0.0) * w
        fin_pass = r["fin_score_w"] >= FIN_SCORE_PASS
        pos_pass = (r["pos"] or 0.0) * w >= POS_PASS
        r["both_value"] = bool(fin_pass and pos_pass)
        r["buy_enabled"] = bool(r["price_low"] and r["sentiment_up"])
        r["sell_enabled"] = bool(r["price_high"] and r["sentiment_down"])
        # marking table
        be, se, bv = r["buy_enabled"], r["sell_enabled"], r["both_value"]
        if be and se:
            r["result"] = "CONTRADICTION"
        elif be and not se and bv:
            r["result"] = "BUY/HOLD"
        elif (not be) and se:
            r["result"] = "SELL"
        elif be and not se and not bv:
            r["result"] = "GATE_OPEN"
        else:
            r["result"] = "NONE"
    return rows


if __name__ == "__main__":
    import config as C2
    prev = [{"ticker": t} for _, t in C2.SEED_TICKERS]
    rows = scan(prev)
    from collections import Counter
    print("rows:", len(rows))
    print("result dist:", dict(Counter(r["result"] for r in rows)))
    print("sentiment_source dist:", dict(Counter(r["source"] for r in rows)))
    print("buy_enabled:", sum(1 for r in rows if r["buy_enabled"]),
          "sell_enabled:", sum(1 for r in rows if r["sell_enabled"]),
          "both_value_pass:", sum(1 for r in rows if r["both_value"]))
    print("insufficient article history (no scored any window & no vader):",
          sum(1 for r in rows if r["n_recent"] == 0 and r["n_prior"] == 0))
    for r in rows:
        if r["result"] in ("BUY/HOLD", "SELL", "CONTRADICTION") or r["_err"]:
            print(f"  {r['ticker']:8s} result={r['result']:12s} src={r['source']} "
                  f"z={r.get('z')} fin={r.get('fin_score_w')} pos={r.get('pos_w')} "
                  f"err={r['_err'] or ''}")
