#!/usr/bin/env python3
"""
calculations.py – Scoring functions with missing-data exclusion.
"""
import math
import pandas as pd
from difflib import get_close_matches

FUZZY_CUTOFF = 0.9

# ---------- Core helpers ----------
def fuzzy_get_series_aliases(df, targets, cutoff=FUZZY_CUTOFF):
    if df is None or df.empty:
        return None
    for target in targets:
        matches = get_close_matches(target, df.index, n=1, cutoff=cutoff)
        if matches:
            series = df.loc[matches[0]].dropna()
            if not series.empty:
                return series
    return None

def get_value_fallback(df, targets, col=0):
    series = fuzzy_get_series_aliases(df, targets)
    if series is None or len(series) == 0:
        return None, None, True
    if len(series) > col:
        val = series.iloc[col]
        if pd.notna(val):
            return val, col, False
    if col > 0 and len(series) > col-1:
        val = series.iloc[col-1]
        if pd.notna(val):
            return val, col-1, True
    return None, None, True

def get_window_fallback(df, targets, n, start=0):
    series = fuzzy_get_series_aliases(df, targets)
    if series is None or len(series) == 0:
        return None, None, True
    if len(series) >= start + n:
        window = series.iloc[start:start+n]
        if not window.isna().any():
            return window.tolist(), start, False
    if start > 0 and len(series) >= (start-1) + n:
        window = series.iloc[start-1:start-1+n]
        if not window.isna().any():
            return window.tolist(), start-1, True
    available = min(len(series), n)
    if available > 0:
        window = series.iloc[:available]
        if not window.isna().any():
            return window.tolist(), 0, True
    return None, None, True

# ---------- Positivity ----------
def compute_positivity(price_df):
    """Fraction of trailing up-days measured close-to-close (close > prior
    close). Works with close-only daily data (e.g. eulerpool candles)."""
    if price_df is None or price_df.empty:
        return 0.5, True
    closes = price_df['Close'].dropna()
    if len(closes) < 2:
        return 0.5, True
    n = 126
    if len(closes) < n + 1:
        n = max(1, len(closes) - 1)
    changes = closes.diff().iloc[-n:]          # up-day = positive day change
    return int((changes > 0).sum()) / n, False

# ---------- Size (small-cap) weighting ----------
def industry_size_percentile(mcaps, industries):
    """Industry-relative market-cap percentile (0-100) per firm, ranked within
    its industry/sector so it is portable across sectors."""
    import pandas as pd
    s = pd.Series(range(len(mcaps)), name="i")
    df = pd.DataFrame({"mcap": list(mcaps), "ind": list(industries)})
    out = {}
    for ind, grp in df.groupby("ind"):
        r = grp["mcap"].rank(pct=True, ascending=True) * 100.0
        for idx, val in zip(grp.index, r):
            out[int(idx)] = float(val)
    return out


def size_percentile(mcaps):
    """Universe-wide market-cap percentile (0-100) per firm, ranked across all
    names (NOT industry-relative)."""
    import pandas as pd
    s = pd.Series(list(mcaps))
    return {int(i): float(p * 100.0) for i, p in enumerate(s.rank(pct=True, ascending=True))}


def size_weight(s, A=0.5, mu=30.0, L=25.0):
    """Shared small-cap amplification: w=1 at both tails, rising smoothly to
    1+A inside the band |s-mu|<=L (cosine bump). micro / large caps keep ~1,
    the small-cap sweet spot (s ~ mu) gets boosted."""
    if abs(s - mu) > L:
        return 1.0
    return 1.0 + A * 0.5 * (1.0 + math.cos(math.pi * (s - mu) / L))


# ---------- Raw Z-score ----------
def compute_zscore(price_df, period=21):
    if price_df is None or price_df.empty:
        return None, True
    closes = price_df['Close'].dropna()
    if len(closes) < 2:
        return None, True

    n = period
    if len(closes) >= n:
        window = closes.iloc[-n:]
        mean = window.mean()
        std = window.std()
        if std == 0:
            return 0.0, False
        current_price = closes.iloc[-1]
        raw_z = (current_price - mean) / std
        return raw_z, False

    if len(closes) >= n + 1:
        window = closes.iloc[-(n+1):-1]
        mean = window.mean()
        std = window.std()
        if std == 0:
            return 0.0, True
        current_price = closes.iloc[-1]
        raw_z = (current_price - mean) / std
        return raw_z, True

    if len(closes) >= 3:
        window = closes.iloc[-len(closes):]
        mean = window.mean()
        std = window.std()
        if std == 0:
            return 0.0, True
        current_price = closes.iloc[-1]
        raw_z = (current_price - mean) / std
        return raw_z, True

    return None, True

# ---------- F-score (returns positive_count, total_available, incomplete) ----------
def compute_fscore(fin, bal, cf):
    if fin is None or bal is None or cf is None:
        return 0, 0, True

    # Aliases (strict synonyms)
    NI_ALIASES = ['Net Income', 'Net Income Common Stockholders', 'Net Income Continuous Operations', 'Net Earnings', 'Net Profit']
    ASSETS_ALIASES = ['Total Assets', 'Assets', 'Total Assets And Goodwill']
    CFO_ALIASES = ['Operating Cash Flow', 'Cash Flow From Continuing Operating Activities', 'Cash From Operations', 'Cash Provided by Operating Activities', 'Net Cash From Operating Activities']
    LT_DEBT_ALIASES = ['Long Term Debt', 'Long Term Debt And Capital Lease Obligation', 'Non Current Debt']
    CA_ALIASES = ['Current Assets', 'Total Current Assets']
    CL_ALIASES = ['Current Liabilities', 'Total Current Liabilities']
    REV_ALIASES = ['Total Revenue', 'Revenue', 'Net Sales', 'Sales']
    COGS_ALIASES = ['Cost Of Revenue', 'Cost of Goods Sold', 'Cost of Sales']
    SHARES_ALIASES = ['Ordinary Shares Number', 'Common Stock Shares Outstanding', 'Shares Outstanding']

    signals = []  # list of 1/0 for available signals
    incomplete = False

    def get_val(df, targets, col=0):
        val, _, inc = get_value_fallback(df, targets, col)
        return val, inc

    # Signal 1: ROA > 0
    ni_cur, inc1 = get_val(fin, NI_ALIASES, 0)
    assets_cur, inc2 = get_val(bal, ASSETS_ALIASES, 0)
    if ni_cur is None or assets_cur is None or assets_cur == 0:
        incomplete = True
    else:
        roa = ni_cur / assets_cur
        signals.append(1 if roa > 0 else 0)
        incomplete = incomplete or inc1 or inc2

    # Signal 2: CFO > 0
    cfo_cur, inc3 = get_val(cf, CFO_ALIASES, 0)
    if cfo_cur is None:
        incomplete = True
    else:
        signals.append(1 if cfo_cur > 0 else 0)
        incomplete = incomplete or inc3

    # Signal 3: ROA improvement
    ni_prev, inc4 = get_val(fin, NI_ALIASES, 1)
    assets_prev, inc5 = get_val(bal, ASSETS_ALIASES, 1)
    if ni_cur is None or assets_cur is None or assets_cur == 0 or ni_prev is None or assets_prev is None or assets_prev == 0:
        incomplete = True
    else:
        roa_cur = ni_cur / assets_cur
        roa_prev = ni_prev / assets_prev
        signals.append(1 if roa_cur > roa_prev else 0)
        incomplete = incomplete or inc4 or inc5

    # Signal 4: CFO > NI
    if cfo_cur is None or ni_cur is None:
        incomplete = True
    else:
        signals.append(1 if cfo_cur > ni_cur else 0)

    # Signal 5: Leverage decrease
    lt_cur, inc6 = get_val(bal, LT_DEBT_ALIASES, 0)
    lt_prev, inc7 = get_val(bal, LT_DEBT_ALIASES, 1)
    if lt_cur is None or assets_cur is None or assets_cur == 0 or lt_prev is None or assets_prev is None or assets_prev == 0:
        incomplete = True
    else:
        lev_cur = lt_cur / assets_cur
        lev_prev = lt_prev / assets_prev
        signals.append(1 if lev_cur < lev_prev else 0)
        incomplete = incomplete or inc6 or inc7

    # Signal 6: Current ratio improvement
    ca_cur, inc8 = get_val(bal, CA_ALIASES, 0)
    cl_cur, inc9 = get_val(bal, CL_ALIASES, 0)
    ca_prev, inc10 = get_val(bal, CA_ALIASES, 1)
    cl_prev, inc11 = get_val(bal, CL_ALIASES, 1)
    if ca_cur is None or cl_cur is None or cl_cur == 0 or ca_prev is None or cl_prev is None or cl_prev == 0:
        incomplete = True
    else:
        cr_cur = ca_cur / cl_cur
        cr_prev = ca_prev / cl_prev
        signals.append(1 if cr_cur > cr_prev else 0)
        incomplete = incomplete or inc8 or inc9 or inc10 or inc11

    # Signal 7: No new shares
    shares_cur, inc12 = get_val(bal, SHARES_ALIASES, 0)
    shares_prev, inc13 = get_val(bal, SHARES_ALIASES, 1)
    if shares_cur is None or shares_prev is None:
        incomplete = True
    else:
        signals.append(1 if shares_cur <= shares_prev else 0)
        incomplete = incomplete or inc12 or inc13

    # Signal 8: Gross margin improvement
    rev_cur, inc14 = get_val(fin, REV_ALIASES, 0)
    cogs_cur, inc15 = get_val(fin, COGS_ALIASES, 0)
    rev_prev, inc16 = get_val(fin, REV_ALIASES, 1)
    cogs_prev, inc17 = get_val(fin, COGS_ALIASES, 1)
    if rev_cur is None or cogs_cur is None or rev_cur == 0 or rev_prev is None or cogs_prev is None or rev_prev == 0:
        incomplete = True
    else:
        gm_cur = (rev_cur - cogs_cur) / rev_cur
        gm_prev = (rev_prev - cogs_prev) / rev_prev
        signals.append(1 if gm_cur > gm_prev else 0)
        incomplete = incomplete or inc14 or inc15 or inc16 or inc17

    # Signal 9: Asset turnover improvement
    at_cur = rev_cur / assets_cur if (rev_cur is not None and assets_cur is not None and assets_cur != 0) else None
    at_prev = rev_prev / assets_prev if (rev_prev is not None and assets_prev is not None and assets_prev != 0) else None
    if at_cur is None or at_prev is None:
        incomplete = True
    else:
        signals.append(1 if at_cur > at_prev else 0)

    positive = sum(signals)
    total = len(signals)
    return positive, total, incomplete

# ---------- G-score (returns positive_count, total_available, incomplete) ----------
def compute_gscore(q_fin, q_cf):
    if q_fin is None or q_cf is None:
        return 0, 0, True

    NI_ALIASES = ['Net Income', 'Net Income Common Stockholders', 'Net Income Continuous Operations', 'Net Earnings', 'Net Profit']
    CFO_ALIASES = ['Operating Cash Flow', 'Cash Flow From Continuing Operating Activities', 'Cash From Operations', 'Cash Provided by Operating Activities', 'Net Cash From Operating Activities']
    RD_ALIASES = ['Research And Development', 'R&D', 'Research and Development Expense']
    REV_ALIASES = ['Total Revenue', 'Revenue', 'Net Sales', 'Sales']
    CAPEX_ALIASES = ['Capital Expenditure', 'Capital Expenditures', 'Purchase Of PPE', 'Purchase of Property, Plant and Equipment']

    signals = []
    incomplete = False

    def get_val(df, targets, col=0):
        val, _, inc = get_value_fallback(df, targets, col)
        return val, inc

    def get_ttm_sum(df, targets, n=4):
        win, _, inc = get_window_fallback(df, targets, n, start=0)
        if win is not None:
            return sum(win), inc
        return None, True

    # Signal 1: TTM NI > 0
    ni_ttm, inc1 = get_ttm_sum(q_fin, NI_ALIASES, 4)
    if ni_ttm is None:
        incomplete = True
    else:
        signals.append(1 if ni_ttm > 0 else 0)
        incomplete = incomplete or inc1

    # Signal 2: TTM CFO > 0
    cfo_ttm, inc2 = get_ttm_sum(q_cf, CFO_ALIASES, 4)
    if cfo_ttm is None:
        incomplete = True
    else:
        signals.append(1 if cfo_ttm > 0 else 0)
        incomplete = incomplete or inc2

    # Signal 3: NI growth
    ni_series = fuzzy_get_series_aliases(q_fin, NI_ALIASES)
    if ni_series is not None and len(ni_series) >= 8:
        latest = ni_series.iloc[:4].sum()
        prev = ni_series.iloc[4:8].sum()
        signals.append(1 if latest > prev else 0)
    else:
        incomplete = True

    # Signal 4: CFO > NI
    if ni_ttm is not None and cfo_ttm is not None and ni_ttm != 0:
        signals.append(1 if cfo_ttm > ni_ttm else 0)
    else:
        incomplete = True

    # Signal 5: R&D/Revenue volatility < 0.2
    rd_win, _, inc5a = get_window_fallback(q_fin, RD_ALIASES, 5, start=0)
    rev_win, _, inc5b = get_window_fallback(q_fin, REV_ALIASES, 5, start=0)
    if rd_win is not None and rev_win is not None and len(rd_win) == 5 and len(rev_win) == 5:
        if all(v != 0 for v in rev_win):
            ratios = [rd / rev for rd, rev in zip(rd_win, rev_win)]
            mean_r = sum(ratios) / len(ratios)
            if mean_r != 0:
                vol = math.sqrt(sum((r - mean_r)**2 for r in ratios) / len(ratios)) / abs(mean_r)
                signals.append(1 if vol < 0.2 else 0)
            else:
                incomplete = True
        else:
            incomplete = True
        incomplete = incomplete or inc5a or inc5b
    else:
        incomplete = True

    # Signal 6: Capex/Revenue volatility < 0.2
    capex_win, _, inc6a = get_window_fallback(q_cf, CAPEX_ALIASES, 5, start=0)
    rev_win2, _, inc6b = get_window_fallback(q_fin, REV_ALIASES, 5, start=0)
    if capex_win is not None and rev_win2 is not None and len(capex_win) == 5 and len(rev_win2) == 5:
        capex_vals = [abs(x) for x in capex_win]
        if all(v != 0 for v in rev_win2):
            ratios = [c / r for c, r in zip(capex_vals, rev_win2)]
            mean_r = sum(ratios) / len(ratios)
            if mean_r != 0:
                vol = math.sqrt(sum((r - mean_r)**2 for r in ratios) / len(ratios)) / abs(mean_r)
                signals.append(1 if vol < 0.2 else 0)
            else:
                incomplete = True
        else:
            incomplete = True
        incomplete = incomplete or inc6a or inc6b
    else:
        incomplete = True

    # Signal 7: R&D/Revenue increase
    rd8, _, inc7a = get_window_fallback(q_fin, RD_ALIASES, 8, start=0)
    rev8, _, inc7b = get_window_fallback(q_fin, REV_ALIASES, 8, start=0)
    if rd8 is not None and rev8 is not None and len(rd8) == 8 and len(rev8) == 8:
        latest_rd = sum(rd8[:4])
        prev_rd = sum(rd8[4:8])
        latest_rev = sum(rev8[:4])
        prev_rev = sum(rev8[4:8])
        if latest_rev != 0 and prev_rev != 0:
            signals.append(1 if (latest_rd / latest_rev) >= (prev_rd / prev_rev) else 0)
        else:
            incomplete = True
        incomplete = incomplete or inc7a or inc7b
    else:
        incomplete = True

    # Signal 8: neutral (always excluded – not used)
    # We skip neutral signals; they are not counted.

    positive = sum(signals)
    total = len(signals)
    return positive, total, incomplete
