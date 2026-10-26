#!/usr/bin/env python3
"""cascade.py - multi-source ticker resolution cascade (spec §2, steps 1-7).

resolve(cells, source, fetch, tags, write_back) walks, in exact order:
  step 1: the source's OWN cell ticker
  step 2: the other ticker cells (source-specific order, below)
  step 3: (other APIs) is embodied inside `fetch` (news->yfinance, euler->yfinance);
          `fetch_alt_flag` lets a source log when its fetch used an alternate API
  step 4: search the company NAME / ISIN (eulerpool search, then yfinance lookup)
  step 5: retry the fetch with that search result (written back to the own cell)
  step 6: final fallback - yfinance with whatever resolves
  step 7: tag `source_unresolved` instead of returning None silently

Write-back: whenever a fallback CELL (step 2) or a NAME SEARCH (steps 4/5)
supplied the working ticker, `write_back(own_cell_key, ticker)` persists it so the
next run resolves directly. Every fallback is audited via `tags`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eulerpool_fund

def resolve(cells, source, fetch, tags, write_back=None, fetch_alt_flag=None):
    """Single-Ticker cascade (collapsed-Ticker spec):

      1. try the Ticker cell on the target API
      2. fail -> look up the company Name, then ISIN
         (eulerpool search accepts both, yfinance lookup as backstop)
      3. write the found Name/ISIN identifier to its cell (via `write_back`)
      4. retry the target API with Name, then with ISIN
      5. on success, write the working identifier back to the Ticker cell
      6. fail -> tag source_unresolved (never a silent None)

    cells       - {'ticker','company','isin'}
    fetch(tk)   - per-source fetch -> result, or None on failure/quota/empty
                  (multi-key failover expected inside fetch)
    tags        - list to append audit tags to
    write_back(sym) - persist a Name/ISIN-resolved ticker to the Ticker cell
    fetch_alt_flag(tk, res) -> api-name or None (step-3 alternate API audit)
    """
    info = {"source": source}
    ticker = cells.get("ticker")
    # step 1: the Ticker cell on the target API
    if ticker:
        res = fetch(ticker)
        if res is not None:
            info.update({"via": "ticker", "ticker": ticker})
            if fetch_alt_flag:
                alt = fetch_alt_flag(ticker, res)
                if alt:
                    tags.append(f"source_alt={alt}")
                    info["internal_alt"] = True
            return res, info
    # steps 2-5: look up Name then ISIN, retry the API, write back on success
    for ident, how in ((cells.get("company"), "name"), (cells.get("isin"), "isin")):
        if not ident or not str(ident).strip():
            continue
        sym, found_via = search_name(str(ident).strip())
        if not sym:
            continue
        res = fetch(sym)
        if res is not None:
            tags.append(f"ticker_resolved={sym} via={how} ({found_via or 'search'})")
            if write_back:
                write_back(sym)                 # write back to the Ticker cell
            return res, {"via": how, "ticker": sym}
    # step 6: audit as unresolved, never a silent None
    tags.append("source_unresolved")
    return None, {"unresolved": True}



def search_name(name):
    """Step 4: company name or ISIN -> a symbol.

    Tries eulerpool search first (accepts name/ISIN), then a yfinance lookup.
    Returns (symbol, how) or (None, None).
    """
    if not name:
        return None, None
    try:
        t = eulerpool_fund.search(str(name))
        if t:
            return t, "eulerpool_name_search"
    except Exception:
        pass
    try:
        import yfinance as yf
        q = yf.Search(str(name), max_results=1)
        for qt in getattr(q, "quotes", []) or []:
            s = getattr(qt, "symbol", None)
            if s:
                return str(s), "yfinance_name_search"
    except Exception:
        pass
    return None, None



