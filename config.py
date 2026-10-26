#!/usr/bin/env python3
"""config.py - settings and recommended (research-backed) thresholds.

Thresholds can be edited in the 'Absolute Thresholds' block of Portfolio.xlsx;
these defaults are the research-cited values documented in README.md.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Load optional .env (holds API keys) so secrets are never committed.
if os.path.exists(os.path.join(HERE, ".env")):
    for _line in open(os.path.join(HERE, ".env"), encoding="utf-8", errors="ignore"):
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

XLSX_PATH = os.path.join(HERE, "Portfolio.xlsx")
# Restricted-trades document: stocks flagged there are noted in the
# "Important note(s)" column with a reason.
RESTRICTED_DOC = os.path.join(HERE, "Restricted Trades.docx")
# Optional plain-text alternative/override: one entry per line, either
# "TICKER" or "TICKER, reason" (ticker matched case-insensitively).
RESTRICTED_TXT = os.path.join(HERE, "restricted_trades.txt")

# ---- data fetching: 1 s spacing between yfinance calls (fair use) ---------
FETCH_DELAY = float(os.environ.get("FETCH_DELAY", "1.0"))

# ---- sources -----------------------------------------------------------------
# News: stockdata.org (global news, bulk-friendly) tried first, then Yahoo per
# ticker. Finnhub is removed (US-only free tier, adds hassle).
STOCKDATA_API_KEY = os.environ.get("STOCKDATA_API_KEY", "")
# Optional second stockdata.org key, used if the first fails/exhausts its quota.
STOCKDATA_API_KEY_2 = os.environ.get("STOCKDATA_API_KEY_2", "")
# Comma-separated multi-key lists (1..N); preferred over the single-key vars.
# e.g. STOCKDATA_KEYS=key1,key2  /  EULERPOOL_KEYS=key1,key2,key3
STOCKDATA_KEYS = os.environ.get("STOCKDATA_KEYS", "")
EULERPOOL_KEYS = os.environ.get("EULERPOOL_KEYS", "")
# Fixed article-level sentiment SD baseline for the sentiment-change gate,
# routed by scorer (never mixed within a ticker):
#   SENTIMENT_SD_STOCKDATA - used when sentiment_source == 'stockdata'.
#     PROVENANCE: pooled SD over n=26 article-level StockData sentiment_score
#     values read from articles.csv (one 7-day pull across the universe),
#     generated 2026-10-26. PROVISIONAL: n<100 and thin per-ticker coverage
#     (1-2 articles/ticker, 17/40 tickers) - confirm on a larger corpus.
#   SENTIMENT_SD_VADER - used when sentiment_source == 'vader'. PROVENANCE:
#     pooled SD over n=1786 article-level VADER scores from the Finnhub+summary
#     training corpus, 2026-10-25 (per-ticker SDs 0.43-0.53).
# Fixed baseline - no rolling recompute, no corpus accumulation.
SENTIMENT_SD_STOCKDATA = 0.2922
SENTIMENT_SD_VADER = 0.48
# Finnhub (training-only sentiment fill when StockData has zero articles at T-20;
# NEVER used in the live scan path).
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
FINNHUB_KEYS = os.environ.get("FINNHUB_KEYS", "")
# Fundamentals: eulerpool (richer KPI/quality data) with yfinance fallback.
EULERPOOL_API_KEY = os.environ.get("EULERPOOL_API_KEY", "")
# Tickers aren't always interchangeable between sources. Map each source to the
# symbol it needs when it differs from our stored (yfinance) ticker, e.g.
# Roche: yfinance RHHBY, eulerpool ROG.SW.
EULER_TICKER_MAP = {"RHHBY": "ROG.SW"}
NEWS_DAYS_BOOTSTRAP = 3          # how far back when the news cells are empty
NEWS_NEWSCOUNT = 40              # total headlines to pull

# ---- book-to-market regime (strict threshold) --------------------------------
# A company is scored with the PRE-profit (solvency/cash/growth) component set
# when B/M < btm_threshold, else the POST-profit (profitability/quality) set.
# This is a strict cut, not a smoothed blend: applying profit-based signals to
# a genuinely pre-profit (low B/M) company would be pointless even at small
# weight, so they are simply not used. P/B ~3 (B/M 0.33) is the classic
# value/growth divide; edit in the sheet if you prefer another cut.
BTM_THRESHOLD = 0.33

# ---- spreadsheet layout ------------------------------------------------------
DATA_HEADER_ROW = 13
COLS = {
    "company": 0, "ticker": 1, "shares": 2, "price": 3, "currency": 4,
    "country": 5, "industry": 6, "zscore": 7, "positivity": 8,
    "fundamentals": 9, "bm": 10, "news_sentiment": 11, "news_change": 12,
    "target": 13, "value_held": 14, "avg_cost": 15, "profit": 16,
    "prev_shares": 17, "prev_price": 18,
    "action": 19, "reasons": 20,
    "euler_ticker": 21, "sd_ticker": 22, "size": 23, "s_unified": 24,
}
N_COLS = max(COLS.values()) + 1


def default_thresholds():
    """Recommended, research-cited threshold defaults (editable in the sheet)."""
    return {
        "wealth": 0.0,            # read from sheet
        "currency": "EUR",        # base currency, read from sheet
        "positivity": 0.55,       # value: clear majority of up-days (uptrend)
        "fin_health": 0.6,        # value: fraction of financial-health signals that pass
        "z": 1.0,                 # one threshold: BUY at z <= -1, SELL at z >= +1
        "news_floor": 0.2,        # min |change| (0-10) to count as a real move
        "btm_threshold": BTM_THRESHOLD,
    }


# Companies to ensure are present in the watchlist (added on the next scan if
# missing). Most already exist; entries are (full_name, likely_ticker).
SEED_TICKERS = [
    ("Thermo Fisher Scientific Inc.", "TMO"),
    ("Danaher Corp.", "DHR"),
    ("Lonza Group AG", "LONN.SW"),
    ("Roche Holding AG", "RHHBY"),
    ("Novartis AG", "NVS"),
    ("Gilead Sciences Inc.", "GILD"),
    ("Bristol Myers Squibb", "BMY"),
    ("Charles River Laboratories", "CRL"),
    ("Sarepta Therapeutics Inc.", "SRPT"),
    ("Krystal Biotech Inc.", "KRYS"),
    ("Eli Lilly & Co", "LLY"),
    ("Pfizer", "PFE"),
    ("AbbVie Inc.", "ABBV"),
    ("AstraZeneca PLC", "AZN"),
    ("Beam Therapeutics Inc.", "BEAM"),
    ("MaxCyte Inc.", "MXCT"),
    ("Oxford Nanopore Technologies", "ONT.L"),
    ("Arcturus Therapeutics Holdings", "ARCT"),
    ("Autolus Therapeutics plc", "AUTL"),
    ("CARsgen Therapeutics", "2171.HK"),
    ("Allogene Therapeutics Inc.", "ALLO"),
    ("Cabaletta Bio Inc.", "CABA"),
    ("RegenXBio Inc.", "RGNX"),
    ("Editas Medicine Inc.", "EDIT"),
    ("CRISPR Therapeutics AG", "CRSP"),
    ("Intellia Therapeutics Inc.", "NTLA"),
    ("Azenta Inc.", "AZTA"),
    ("Vertex Pharmaceuticals Inc.", "VRTX"),
    ("Twist Bioscience Corp.", "TWST"),
    ("Voyager Therapeutics Inc.", "VYGR"),
    ("Krystal Biotech Inc.", "KRYS"),
    ("Caribou Biosciences Inc.", "CRBU"),
    ("4D Molecular Therapeutics Inc.", "FDMT"),
    ("Cellectis", "CLLS"),
    ("Precision BioSciences Inc.", "DTIL"),
    ("uniQure NV", "QURE"),
    ("Solid Biosciences Inc.", "SLDB"),
    ("Capricor Therapeutics Inc.", "CAPR"),
    ("Prime Medicine Inc.", "PRME"),
    ("Wave Life Sciences Ltd.", "WVE"),
    ("BioNTech", "BNTX"),
]
