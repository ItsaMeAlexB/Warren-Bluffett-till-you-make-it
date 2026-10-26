#!/usr/bin/env python3
"""scores.py - financial-health scoring under a strict book-to-market regime.

A name is scored with either the PRE-profit set (solvency/cash/growth — for
low B/M, often pre-revenue) or the POST-profit set (profitability/quality), the
choice being a single strict B/M threshold rather than a blended curve — it is
pointless to apply profit-based signals to a company with no profit. Both sets
use `info` ratios plus annual statements; inputs that are missing are excluded
(the denominator shrinks) and mark the row incomplete. Eulerpool's rich KPI
bundle (margins, ROA/ROE, Piotroski F-score, Altman, yields, growth) is used as
an independent tie-breaker when available.
"""
from calculations import compute_positivity
import statements as st


def _one(info, key):
    v = info.get(key)
    return v if isinstance(v, (int, float)) and v == v else None


def _net_cash(info):
    cash, debt = _one(info, "totalCash"), _one(info, "totalDebt")
    return (cash - debt) if (cash is not None and debt is not None) else None


def pre_profit_score(info, statements):
    """Solvency / cash / growth health for low B/M — works without profit.

    Uses the broad set of fields yfinance exposes on ~all stocks (info ratios
    plus annual-statement growth when present): liquidity, cash position,
    leverage, cash runway, gross margin, revenue growth and the overinvestment
    check. Missing inputs are excluded (they don't help or hurt)."""
    income, bal, cf = statements
    pos, den, inc = 0, 0, False
    rev = st.revenue(income) if income is not None else []
    ta = st.total_assets(bal) if bal is not None else []

    def ev(cond):
        nonlocal pos, den, inc
        if cond is None:
            inc = True
        else:
            pos += 1 if cond else 0
            den += 1

    mcap, cash = _one(info, "marketCap"), _one(info, "totalCash")
    debt = _one(info, "totalDebt")

    # 1 current ratio
    cr = _one(info, "currentRatio")
    ev(cr >= 1.0 if cr is not None else None)
    # 2 quick ratio
    qr = _one(info, "quickRatio")
    ev(qr >= 0.8 if qr is not None else None)
    # 3 net cash (cash >= debt)
    nc = _net_cash(info)
    ev(nc >= 0 if nc is not None else None)
    # 4 adequate cash runway vs market cap
    ev(cash >= 0.15 * mcap if (cash is not None and mcap) else None)
    # 5 low leverage vs market cap
    ev(debt <= 0.5 * mcap if (debt is not None and mcap) else None)
    # 6 cash runway >= 12 months (or already cash-flow positive)
    ocf = _one(info, "operatingCashflow")
    if ocf is not None:
        if ocf > 0:
            ev(True)
        elif cash is not None and cash > 0:
            ev(cash >= (-ocf))   # cash >= one year of net operating burn
        else:
            ev(None)
    else:
        ev(None)
    # 7 gross margin non-negative
    gm = _one(info, "grossMargins")
    ev(gm is not None and gm >= 0 if gm is not None else None)
    # 8 revenue growth > 0 (info, else statements)
    growth = _one(info, "revenueGrowth")
    if growth is None and len(rev) >= 2 and rev[1]:
        growth = (rev[0] - rev[1]) / abs(rev[1])
    ev(growth > 0 if growth is not None else None)
    # 9 overinvestment: sales must grow at least as fast as total assets
    if len(rev) >= 2 and len(ta) >= 2 and rev[1] and ta[1]:
        rev_g = (rev[0] - rev[1]) / rev[1]
        ass_g = (ta[0] - ta[1]) / ta[1]
        ev(rev_g >= ass_g)
    else:
        ev(None)
    # 10 self-funding: operating cash flow positive (or FCF)
    fcf = _one(info, "freeCashflow")
    ev(ocf > 0 if ocf is not None else (fcf > 0 if fcf is not None else None))
    return pos, den, inc


def post_profit_score(info, statements):
    """Enriched value/profitability quality for high B/M stocks: profitability,
    margins, growth, accruals quality, leverage and turnover — from both info
    ratios and statements, whichever is available."""
    income, bal, cf = statements
    pos, den, inc = 0, 0, False
    ni = st.net_income(income); ta = st.total_assets(bal)
    ocf = st.op_cashflow(cf); ltd = st.long_term_debt(bal)
    ca = st.current_assets(bal); cl = st.current_liabilities(bal)
    rev = st.revenue(income); gp = st.gross_profit(income)

    def ev(cond):
        nonlocal pos, den, inc
        if cond is None:
            inc = True
        else:
            pos += 1 if cond else 0
            den += 1

    roa = _one(info, "returnOnAssets"); roe = _one(info, "returnOnEquity")
    ni_info = _one(info, "netIncomeToCommon")
    ocf_info = _one(info, "operatingCashflow")
    gm = _one(info, "grossMargins"); pm = _one(info, "profitMargins")
    growth = _one(info, "revenueGrowth")
    mcap, debt = _one(info, "marketCap"), _one(info, "totalDebt")

    # 1 ROA > 0
    ev(roa > 0 if roa is not None else
       ((ni[0] / ta[0]) > 0 if (len(ni) and len(ta) and ta[0]) else None))
    # 2 ROE > 0
    ev(roe > 0 if roe is not None else None)
    # 3 net income > 0
    ev(ni_info > 0 if ni_info is not None else (ni[0] > 0 if len(ni) else None))
    # 4 operating cash flow > 0
    ev(ocf_info > 0 if ocf_info is not None else (ocf[0] > 0 if len(ocf) else None))
    # 5 net margin > 0
    ev(pm > 0 if pm is not None else None)
    # 6 gross margin > 0
    ev(gm is not None and gm >= 0 if gm is not None else None)
    # 7 revenue growth > 0
    g = growth
    if g is None and len(rev) >= 2 and rev[1]:
        g = (rev[0] - rev[1]) / abs(rev[1])
    ev(g > 0 if g is not None else None)
    # 8 ROA improving (quality)
    ev((ni[0] / ta[0]) > (ni[1] / ta[1])
       if (len(ni) >= 2 and len(ta) >= 2 and ta[0] and ta[1]) else None)
    # 9 accruals: CFO > NI
    ev(ocf_info > ni_info if (ocf_info is not None and ni_info is not None) else
       (ocf[0] > ni[0]) if (len(ocf) and len(ni)) else None)
    # 10 leverage falling (statements)
    ev((ltd[0] / ta[0]) < (ltd[1] / ta[1])
       if (len(ltd) >= 2 and len(ta) >= 2 and ta[0] and ta[1]) else None)
    # 11 low debt vs market cap
    ev(debt <= 0.5 * mcap if (debt is not None and mcap) else None)
    # 12 current ratio rising (statements), else >= 1 (info)
    cur = ((ca[0] / cl[0]) > (ca[1] / cl[1])
           if (len(ca) >= 2 and len(cl) >= 2 and cl[0] and cl[1]) else None)
    if cur is None:
        cr = _one(info, "currentRatio")
        cur = cr >= 1.0 if cr is not None else None
    ev(cur)
    # 13 gross margin rising (statements)
    ev((gp[0] / rev[0]) > (gp[1] / rev[1])
       if (len(gp) >= 2 and len(rev) >= 2 and rev[0] and rev[1]) else None)
    # 14 overinvestment: sales growth >= asset growth
    if len(rev) >= 2 and len(ta) >= 2 and rev[1] and ta[1]:
        ev(((rev[0] - rev[1]) / rev[1]) >= ((ta[0] - ta[1]) / ta[1]))
    else:
        ev(None)

    if den >= 3:
        return pos, den, inc
    return 0, 0, True


def euler_health(euler):
    """Robust 0-1 quality signal from eulerpool KPI ratios (rich, single-call
    data). Profit-dependent signals are *excluded*, not failed, when the company
    reports no profit — applying "good profit" to a loss-maker is meaningless."""
    if not euler:
        return None
    k = euler.get("kpi") or {}
    pos, den, inc = 0, 0, False

    def n(x):
        v = k.get(x)
        return v if isinstance(v, (int, float)) and v == v else None

    def ev(cond):
        nonlocal pos, den, inc
        if cond is None:
            inc = True
        else:
            pos += 1 if cond else 0
            den += 1

    earn = n("earnings") if n("earnings") is not None else n("netIncome")
    loss = earn is not None and earn <= 0

    # non-profit signals always apply
    ev(n("gross_margin") >= 0 if n("gross_margin") is not None else None)
    g = n("revenue_growth")
    ev(g > 0 if g is not None else None)
    cur = n("current_ratio"); ev(cur >= 1.0 if cur is not None else None)
    qui = n("quick_ratio"); ev(qui >= 0.8 if qui is not None else None)
    dte = n("debt_to_equity"); ev(dte < 1.5 if dte is not None else None)
    alt = n("altman_z_score")
    ev(alt > 1.8 if alt is not None else None)

    # profit-dependent signals: skip entirely for a loss-maker
    if loss:
        for key in ("roa", "roe", "earnings_margin", "piotroski_score",
                    "earnings_yield", "fcf_yield", "revenue_cagr_5y"):
            if n(key) is not None:
                inc = True  # available but intentionally not applied
    else:
        ev(n("roa") > 0 if n("roa") is not None else None)
        ev(n("roe") > 0 if n("roe") is not None else None)
        ev(n("earnings_margin") >= 0 if n("earnings_margin") is not None else None)
        pio = n("piotroski_score"); ev(pio >= 5 if pio is not None else None)
        ey = n("earnings_yield"); ev(ey > 0.05 if ey is not None else None)
        fy = n("fcf_yield"); ev(fy > 0.03 if fy is not None else None)
        cagr = n("revenue_cagr_5y"); ev(cagr >= 0 if cagr is not None else None)
    return (pos / den) if den else 0.0, den, inc


def _profitable(info, statements, euler):
    """Best-effort: does the company earn a profit? Prefer eulerpool earnings,
    else yfinance info, else statements. None = unknown."""
    if euler and isinstance(euler.get("kpi"), dict):
        k = euler["kpi"]
        v = k.get("earnings") if k.get("earnings") is not None else k.get("netIncome")
        if v is not None:
            return v > 0
    v = _one(info, "netIncomeToCommon")
    if v is not None:
        return v > 0
    income = statements[0] if statements else None
    ni = st.net_income(income) if income is not None else []
    return (ni[0] > 0) if ni else None


def financial_health(info, statements, btm, cfg, euler=None):
    """Return (score, label, incomplete).

    Regime is chosen by *profitability*, not B/M: if the company earns a profit
    the POST-profit (profitability/quality) signals apply; if it is loss-making
    (or unknown) the PRE-profit (solvency/cash/growth, no profit terms) set is
    used. Applying profit signals to a company with no profit is meaningless,
    so they are simply not applied."""
    profitable = _profitable(info, statements, euler)
    if profitable:
        p, d, inc = post_profit_score(info, statements)
        label = "POST"
    else:
        p, d, inc = pre_profit_score(info, statements)
        label = "PRE"

    score = (p / d) if d else 0.0
    incomplete = inc

    # eulerpool gives a much richer, independent signal (margins, ROA/ROE,
    # Piotroski F-score, Altman, yields, growth) -> use it as the tie-breaker.
    eh = euler_health(euler)
    if eh is not None:
        escore, eden, einc = eh
        score = 0.5 * score + 0.5 * escore
        incomplete = incomplete or einc
    return round(score, 4), label, incomplete


def positivity(price_df):
    return compute_positivity(price_df)
