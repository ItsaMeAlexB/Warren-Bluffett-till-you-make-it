#!/usr/bin/env python3
"""statements.py - pull key income / balance / cash-flow items from yfinance
annual statements. Concise sanity-ping that statements are usable for the
Piotroski-style (POST-profit) score."""

from difflib import get_close_matches


def series(df, aliases, cutoff=0.82):
    """Return the matched row as a list of numeric values, newest first.
    yfinance statements use fiscal-year columns ordered most-recent first."""
    if df is None or getattr(df, "empty", True):
        return []
    idx = list(df.index)
    for a in aliases:
        m = get_close_matches(a, idx, n=1, cutoff=cutoff)
        if m:
            row = df.loc[m[0]].dropna()
            vals = []
            for v in row.tolist():
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if v == v:  # not NaN
                    vals.append(v)
            if vals:
                return vals  # newest first
    return []


# Concept aliases -----------------------------------------------------------------
def net_income(income):
    return series(income, [
        "Net Income", "Net Income Common Stockholders", "Net Earnings",
        "Net Income Continuous Operations",
    ])


def total_assets(balance):
    return series(balance, ["Total Assets", "Assets",
                            "Total Assets And Goodwill"])


def op_cashflow(cashflow):
    return series(cashflow, [
        "Operating Cash Flow", "Net Cash From Operating Activities",
        "Cash From Operations", "Cash Provided by Operating Activities",
    ])


def long_term_debt(balance):
    return series(balance, [
        "Long Term Debt", "Long Term Debt And Capital Lease Obligation",
        "Non Current Debt",
    ])


def current_assets(balance):
    return series(balance, ["Total Current Assets", "Current Assets"])


def current_liabilities(balance):
    return series(balance, ["Total Current Liabilities", "Current Liabilities"])


def revenue(income):
    return series(income, ["Total Revenue", "Operating Revenue", "Revenues"])


def gross_profit(income):
    return series(income, ["Gross Profit"])
