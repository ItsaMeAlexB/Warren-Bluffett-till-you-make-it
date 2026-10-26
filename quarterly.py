#!/usr/bin/env python3
"""quarterly.py - financial waterfall for the pass (spec D + spec 1).

financial_waterfall(yf_tk, eu_tk, info, statements, btm, euler) implements the
5-step order: yfinance quarterly > eulerpool quarterly > yfinance annual >
eulerpool annual > exclude. Notes are emitted ONLY for annual_fallback /
no_financials (clean notes). eulerpool is queried lazily only in fallback steps.

Level components use the most recent quarter; delta components use the
year-ago same quarter (YoY, index offset 4), gated by a 4-quarter gap check.
Missing fields are excluded (None).
"""
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import statements as st
import eulerpool_fund                      # module name (lazy fetch in waterfall)
from eulerpool_fund import _get          # key failover + 1 s spacing


def _f(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _lookup(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            v = _f(d[k])
            if v is not None:
                return v, k
    return None, None


def _yoy_quarter(list_, field, *aliases, offset=4):
    """(level, yoy) across an ASCENDING (oldest-first) list of dicts.
    level = newest (last); yoy = `offset` periods before newest (same quarter
    prior year). Returns (lv, yv)."""
    lv = None
    if list_ and isinstance(list_[-1], dict):
        lv, _ = _lookup(list_[-1], field, *aliases)
    yv = None
    if len(list_) > offset:
        yv, _ = _lookup(list_[-1 - offset], field, *aliases)
    return lv, yv


def _qtr_label(p):
    """'2026-06-30' / '2026-06-30e' -> 'Q2_2026'; else short date or None."""
    s = str(p or "").strip().rstrip("e").strip()
    try:
        import datetime
        d = datetime.datetime.strptime(s[:10], "%Y-%m-%d")
        return "Q%d_%d" % ((d.month - 1) // 3 + 1, d.year)
    except Exception:
        return s[:7] or None


def _actual(list_):
    """Drop estimated/forward rows (period ends in 'e') and non-dict rows."""
    out = []
    for d in list_:
        if not isinstance(d, dict):
            continue
        p = d.get("period") or d.get("quarterPeriod")
        s = str(p or "").strip()
        if not s or s.endswith("e") or s.endswith("E"):
            continue
        out.append(d)
    return out


def _pdate(x):
    """Best-effort datetime from a period string or pandas Timestamp; else None."""
    try:
        if hasattr(x, "year") or hasattr(x, "strftime"):
            return x if hasattr(x, "month") else None
    except Exception:
        pass
    s = str(x or "").strip().rstrip("e").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return _dt.datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _gap_ok(cur, yoy):
    """True when cur and yoy are ~4 quarters apart (YoY), not a blind offset.

    Accepts ~320-400 days (normal fiscal quarters ~90d x4 ~= 360d). If either
    date is unknown, assume aligned (can't verify)."""
    if not cur or not yoy:
        return True
    d = abs((cur - yoy).days)
    return 300 <= d <= 410


def _is_gapped(list_):
    """True when the YoY pair (newest vs 4-back) is NOT ~4 quarters apart
    (missing quarter) or there isn't enough history for YoY."""
    if len(list_) < 5 or not isinstance(list_[-1], dict):
        return True
    cur = _pdate(list_[-1].get("period") or list_[-1].get("quarterPeriod"))
    yoy = _pdate(list_[-5].get("period") or list_[-5].get("quarterPeriod"))
    return not _gap_ok(cur, yoy)


def _euler_components(tk):
    """Compute components from eulerpool quarterly statements."""
    inc = _actual(_get("equity/income-statement-quarterly", tk) or [])
    bal = _actual(_get("equity/balance-sheet-quarterly", tk) or [])
    cf = _actual(_get("equity/cashflow-statement-quarterly", tk) or [])
    if not (inc and bal and cf):
        return None, None, True
    qtr = None
    if isinstance(inc[-1], dict):
        qtr = _qtr_label(inc[-1].get("period") or inc[-1].get("quarterPeriod"))

    # YoY alignment (spec 9): deltas are only valid if the two periods are ~4
    # quarters apart; a missing quarter must not look like a real YoY change.
    inc_gap = _is_gapped(inc)
    bal_gap = _is_gapped(bal)
    gapped = inc_gap or bal_gap

    rev0, rev1 = _yoy_quarter(inc, "revenue")
    gp0, gp1 = _yoy_quarter(inc, "grossIncome", "grossincome")
    ni0, ni1 = _yoy_quarter(inc, "netIncome", "earnings")
    rd0, _ = _yoy_quarter(inc, "researchDevelopment")
    ta0, ta1 = _yoy_quarter(bal, "totalAssets")
    ca0, ca1 = _yoy_quarter(bal, "currentAssets")
    cl0, cl1 = _yoy_quarter(bal, "currentLiabilities", "totalCurrentLiabilities")
    debt0, debt1 = _yoy_quarter(bal, "longTermDebt", "totalDebt")
    ocf0, _ = _yoy_quarter(cf, "netOperatingCashFlow", "operatingCashflow", "cashFromOperations")
    capex0, _ = _yoy_quarter(cf, "capex", "capitalExpenditure")

    roa = (ni0 / ta0) if (ni0 is not None and ta0) else None
    roa_y = (ni1 / ta1) if (ni1 is not None and ta1) else None
    cfo_a = (ocf0 / ta0) if (ocf0 is not None and ta0) else None
    accru = ((ni0 - ocf0) / ta0) if (ni0 is not None and ocf0 is not None and ta0) else None
    d_lev = None
    if debt0 is not None and ta0 and debt1 is not None and ta1 and not bal_gap:
        d_lev = (debt0 / ta0) - (debt1 / ta1)
    d_liq = None
    if ca0 is not None and cl0 and ca1 is not None and cl1 and not bal_gap:
        d_liq = (ca0 / cl0) - (ca1 / cl1)
    gm0 = (gp0 / rev0) if (gp0 is not None and rev0) else None
    gm1 = (gp1 / rev1) if (gp1 is not None and rev1) else None
    d_gm = (gm0 - gm1) if (gm0 is not None and gm1 is not None and not inc_gap) else None
    at0 = (rev0 / ta0) if (rev0 is not None and ta0) else None
    at1 = (rev1 / ta1) if (rev1 is not None and ta1) else None
    d_at = (at0 - at1) if (at0 is not None and at1 is not None and not (inc_gap or bal_gap)) else None
    rd_i = (rd0 / rev0) if (rd0 is not None and rev0) else None
    cap_i = (abs(capex0) / rev0) if (capex0 is not None and rev0) else None
    return {
        "roa": roa,
        "delta_roa": (roa - roa_y) if (roa is not None and roa_y is not None and not (inc_gap or bal_gap)) else None,
        "cfo_assets": cfo_a, "accruals": accru, "delta_leverage": d_lev,
        "delta_liquidity": d_liq, "share_issuance": None,
        "delta_gross_margin": d_gm, "delta_aturn": d_at,
        "rd_intensity": rd_i, "capex_intensity": cap_i,
    }, qtr, gapped


def _yf_quarterly(tk):
    import yfinance as yf
    t = yf.Ticker(tk)
    return t.quarterly_financials, t.quarterly_balance_sheet, t.quarterly_cashflow


def _yf_components(tk):
    inc, bal, cf = _yf_quarterly(tk)
    if (inc is None or getattr(inc, "empty", True)
            or bal is None or getattr(bal, "empty", True)
            or cf is None or getattr(cf, "empty", True)):
        return None, None, True
    cols = list(inc.columns)
    qtr = cols[0].strftime("%Y-%m") if cols else None
    # YoY alignment: newest vs 4-back must be ~4 quarters apart (spec 9)
    gapped = (len(cols) < 5) or not _gap_ok(_pdate(cols[0]), _pdate(cols[4] if len(cols) > 4 else None))

    def ss(df, aliases):
        vals = st.series(df, aliases)          # newest first
        return (vals[0] if len(vals) > 0 else None,
                vals[4] if len(vals) > 4 else None)

    ni0, ni1 = ss(inc, ["Net Income", "Net Income Common Stockholders"])
    rev0, rev1 = ss(inc, ["Total Revenue", "Operating Revenue"])
    gp0, gp1 = ss(inc, ["Gross Profit"])
    rd0, _ = ss(inc, ["Research And Development", "R&D Expense"])
    ta0, ta1 = ss(bal, ["Total Assets"])
    ca0, ca1 = ss(bal, ["Total Current Assets", "Current Assets"])
    cl0, cl1 = ss(bal, ["Total Current Liabilities", "Current Liabilities"])
    debt0, debt1 = ss(bal, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"])
    ocf0, _ = ss(cf, ["Operating Cash Flow", "Cash From Operations"])
    capex0, _ = ss(cf, ["Capital Expenditure", "Capital Expenditures"])

    roa = (ni0 / ta0) if (ni0 is not None and ta0) else None
    roa_y = (ni1 / ta1) if (ni1 is not None and ta1) else None
    cfo_a = (ocf0 / ta0) if (ocf0 is not None and ta0) else None
    accru = ((ni0 - ocf0) / ta0) if (ni0 is not None and ocf0 is not None and ta0) else None
    d_lev = None
    if debt0 is not None and ta0 and debt1 is not None and ta1 and not gapped:
        d_lev = (debt0 / ta0) - (debt1 / ta1)
    d_liq = None
    if ca0 is not None and cl0 and ca1 is not None and cl1 and not gapped:
        d_liq = (ca0 / cl0) - (ca1 / cl1)
    gm0 = (gp0 / rev0) if (gp0 is not None and rev0) else None
    gm1 = (gp1 / rev1) if (gp1 is not None and rev1) else None
    d_gm = (gm0 - gm1) if (gm0 is not None and gm1 is not None and not gapped) else None
    at0 = (rev0 / ta0) if (rev0 is not None and ta0) else None
    at1 = (rev1 / ta1) if (rev1 is not None and ta1) else None
    d_at = (at0 - at1) if (at0 is not None and at1 is not None and not gapped) else None
    d_roa = (roa - roa_y) if (roa is not None and roa_y is not None and not gapped) else None
    # spec 9: a gapped quarterly delta is recomputed from ANNUAL (not excluded)
    if gapped:
        try:
            import yfinance as yf
            t = yf.Ticker(tk)
            ay = _annual_yoy_deltas(
                st.series(t.financials, ["Net Income", "Net Income Common Stockholders"]),
                st.series(t.balance_sheet, ["Total Assets"]),
                st.series(t.financials, ["Total Revenue", "Operating Revenue"]),
                st.series(t.financials, ["Gross Profit"]),
                st.series(t.balance_sheet, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"]),
                st.series(t.balance_sheet, ["Total Current Assets", "Current Assets"]),
                st.series(t.balance_sheet, ["Total Current Liabilities", "Current Liabilities"]))
            if ay["delta_roa"] is not None:
                d_roa = ay["delta_roa"]
            if ay["delta_gross_margin"] is not None:
                d_gm = ay["delta_gross_margin"]
            if ay["delta_aturn"] is not None:
                d_at = ay["delta_aturn"]
            if ay["delta_leverage"] is not None:
                d_lev = ay["delta_leverage"]
            if ay["delta_liquidity"] is not None:
                d_liq = ay["delta_liquidity"]
        except Exception:
            pass
    rd_i = (rd0 / rev0) if (rd0 is not None and rev0) else None
    cap_i = (abs(capex0) / rev0) if (capex0 is not None and rev0) else None
    return {
        "roa": roa, "delta_roa": d_roa, "cfo_assets": cfo_a, "accruals": accru,
        "delta_leverage": d_lev, "delta_liquidity": d_liq, "share_issuance": None,
        "delta_gross_margin": d_gm, "delta_aturn": d_at,
        "rd_intensity": rd_i, "capex_intensity": cap_i,
    }, qtr, gapped


def fundamentals_score(comp):
    """Fraction of present quarterly components that pass their 'good' test."""
    good = {"roa": lambda x: x > 0, "delta_roa": lambda x: x > 0,
            "cfo_assets": lambda x: x > 0, "accruals": lambda x: x < 0,
            "delta_leverage": lambda x: x < 0, "delta_liquidity": lambda x: x > 0,
            "share_issuance": lambda x: x <= 0, "delta_gross_margin": lambda x: x > 0,
            "delta_aturn": lambda x: x > 0, "rd_intensity": lambda x: x > 0,
            "capex_intensity": lambda x: x > 0}
    pos = den = 0
    for k, fn in good.items():
        v = comp.get(k)
        if v is None:
            continue
        den += 1
        pos += 1 if fn(v) else 0
    return (pos / den) if den else None


def _annual_yoy_deltas(ni, ta, rev, gp, ltd, ca, cl):
    """Annual YoY deltas from newest-first annual series (index0 vs index1).

    Used to replace a gapped (/cleans/ spec 9) quarterly delta with its annual
    equivalent; the quarterly gap is skipped, not silently compared wrong.
    """
    def r0r1(s):
        return (s[0] if len(s) > 0 else None, s[1] if len(s) > 1 else None)

    ni0, ni1 = r0r1(ni); ta0, ta1 = r0r1(ta)
    rev0, rev1 = r0r1(rev); gp0, gp1 = r0r1(gp)
    ltd0, ltd1 = r0r1(ltd); ca0, ca1 = r0r1(ca); cl0, cl1 = r0r1(cl)

    roa0 = (ni0 / ta0) if (ni0 is not None and ta0) else None
    roa1 = (ni1 / ta1) if (ni1 is not None and ta1) else None
    d_roa = (roa0 - roa1) if (roa0 is not None and roa1 is not None) else None
    gm0 = (gp0 / rev0) if (gp0 is not None and rev0) else None
    gm1 = (gp1 / rev1) if (gp1 is not None and rev1) else None
    d_gm = (gm0 - gm1) if (gm0 is not None and gm1 is not None) else None
    at0 = (rev0 / ta0) if (rev0 is not None and ta0) else None
    at1 = (rev1 / ta1) if (rev1 is not None and ta1) else None
    d_at = (at0 - at1) if (at0 is not None and at1 is not None) else None
    d_lev = None
    if ltd0 is not None and ta0 and ltd1 is not None and ta1:
        d_lev = (ltd0 / ta0) - (ltd1 / ta1)
    d_liq = None
    if ca0 is not None and cl0 and ca1 is not None and cl1:
        d_liq = (ca0 / cl0) - (ca1 / cl1)
    return {"delta_roa": d_roa, "delta_gross_margin": d_gm, "delta_aturn": d_at,
            "delta_leverage": d_lev, "delta_liquidity": d_liq}


SUFFICIENT_MIN_COMPS = 4


def _sufficient(comp):
    """True when a quarterly component set is complete enough to stand alone.

    Spec 1: a quarterly pull that is MISSING the cashflow signal (cfo_assets)
    is 'insufficient' and must fall through to the next source - it must not be
    used as if it were a full quarterly. Also require a minimum number of
    present components.
    """
    if not comp:
        return False
    present = sum(1 for v in comp.values() if v is not None)
    if comp.get("cfo_assets") is None:
        return False            # cashflow missing -> insufficient
    return present >= SUFFICIENT_MIN_COMPS


def financial_waterfall(yf_tk, eu_tk, info=None, statements=None, btm=None, euler=None):
    """5-step financial waterfall (spec 1), in exact order:

      1. yfinance quarterly (primary - no eulerpool call)
      2. eulerpool quarterly (only when yfinance quarterly missing)
      3. yfinance annual  -> note 'annual_fallback'
      4. eulerpool annual -> note 'annual_fallback'
      5. exclude from scoring -> note 'no_financials'

    Notes are emitted ONLY for annual_fallback / no_financials; a normal
    quarterly row carries no note and no yfinance-vs-eulerpool source note.
    eulerpool is queried lazily (only in steps 2 and 4), which slashes calls.
    `euler` may be pre-fetched (KPI); if still None and step 4 is reached it is
    fetched lazily here.
    Returns {ok, score, used, gapped, notes}.
    """
    # 1) yfinance quarterly (primary - no eulerpool call)
    try:
        comp, _q, gapped = _yf_components(yf_tk) if yf_tk else (None, None, True)
        if comp is not None and _sufficient(comp):
            return {"ok": True, "score": fundamentals_score(comp),
                    "used": "yf_quarterly", "gapped": bool(gapped), "notes": []}
    except Exception:
        pass
    # 2) eulerpool quarterly (lazy - only reached when yfinance quarterly
    #    is empty OR insufficient, so partial cashflow-missing yf falls through)
    try:
        comp, _q, gapped = _euler_components(eu_tk) if eu_tk else (None, None, True)
        if comp is not None and _sufficient(comp):
            return {"ok": True, "score": fundamentals_score(comp),
                    "used": "eulerpool_quarterly", "gapped": bool(gapped), "notes": []}
    except Exception:
        pass
    # 3) yfinance annual
    if yf_tk and (info or any(s is not None for s in (statements or []))):
        from scores import financial_health
        sc = financial_health(info or {}, statements, btm,
                              C_defaults(), None)[0]
        return {"ok": True, "score": sc, "used": "yf_annual",
                "gapped": False, "notes": ["annual_fallback"]}
    # 4) eulerpool annual (fallback) - lazy KPI fetch only when reached
    if euler is None:
        try:
            euler = eulerpool_fund.fetch(eu_tk) if eu_tk else None
        except Exception:
            euler = None
    if euler:
        from scores import financial_health
        sc = financial_health(info or {}, statements, btm,
                              C_defaults(), euler)[0]
        return {"ok": True, "score": sc, "used": "eulerpool_annual",
                "gapped": False, "notes": ["annual_fallback"]}
    # 5) exclude
    return {"ok": False, "score": None, "used": "none",
            "gapped": False, "notes": ["no_financials"]}


def C_defaults():
    from config import default_thresholds
    return default_thresholds()


if __name__ == "__main__":
    import sys
    tks = sys.argv[1:] or ["BEAM", "ASML"]
    for t in tks:
        r = financial_waterfall(t, t)
        print(t, "used=", r["used"], "score=", r["score"],
              "notes=", r["notes"], "ok=", r["ok"])

