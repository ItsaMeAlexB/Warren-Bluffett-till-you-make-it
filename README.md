# Warren-Stuffett – Daily Value Scanner (Linux, on-demand)

A value-investing scan you run any time: it reads `Portfolio.xlsx`, pulls
current data from **yfinance**, computes value + timing signals, works out an
**equal-weight allocation** of the Wealth cell across the value stocks that
pass your thresholds, and writes back BUY/SELL/HOLD recommendations (with
suggested share quantities) plus an **Important note(s)** column (reasons for
concern, missing fundamentals, high withholding tax, etc.). No Google Sheets,
no SimFin, no Telegram, no background service.

```bash
python3 clean_pass.py --write            # run a scan now
```

---

## How scoring works

### Book-to-market adaptive blend
`B/M = 1 / priceToBook`. Two financial-health scores are **blended smoothly**
by B/M (not a hard cut), so a company just past the boundary isn't suddenly
held to a different, harsher standard:

- `B/M <= btm_low` (0.2, i.e. P/B >= 5) → **mostly pre-profit**
- `B/M = btm_mid` (0.33, i.e. P/B = 3) → **50/50**
- `B/M >= btm_high` (0.5, i.e. P/B <= 2) → **mostly post-profit**
- The transition follows a smooth **logistic** curve
  `w_post = 1/(1+exp(-k*(B/M-0.33)))` with k=18 — not a hard cut and not a
  naive linear 50/50.

All components are fields yfinance exposes on essentially every watchlist
ticker; missing values are **excluded** from the score (neither help nor hurt,
the denominator shrinks) and a `*` is appended to the cell.

**PRE-profit score** (solvency/cash/growth — works with no profit): current
ratio >= 1, quick ratio >= 0.8, net cash, cash >= 15% of market cap, debt <=
50% of market cap, cash runway >= 12 months (or cash-flow positive), gross
margin >= 0, revenue growth > 0, and the overinvestment check (sales growing at
least as fast as assets).

**POST-profit score** (enriched profitability/quality): ROA > 0, ROE > 0, net
income > 0, CFO > 0, net margin > 0, gross margin >= 0, revenue growth > 0,
ROA improving, CFO > NI (accruals), leverage falling + low debt, current ratio
rising, gross margin rising, and the overinvestment check. Both scores draw on
`info` ratios (present on essentially every ticker) plus annual statements when
available; missing inputs only shrink the denominator.

### Positivity (value/trend)
Fraction of up-days over the trailing window (from `calculations.py`).

### News sentiment (momentum)
Headlines come from **stockdata.org** (global news; key in `.env` /
`STOCKDATA_API_KEY`), falling back to the Yahoo Finance search endpoint when it
errors/times out. **Primary sentiment is StockData entity sentiment**:
`/news/all?filter_entities=true` returns per-symbol `entities[].sentiment_score`
(-1..+1) weighted by `match_score`, bucketed into three **rolling 24h windows**
relative to run time T (day1=(T-72h,T-48h], day2=(T-48h,T-24h], day3=(T-24h,T]).
**VADER is only the fallback** when StockData returns zero scored articles or the
call fails (fallback news comes from Yahoo; it has no sentiment endpoint, so
VADER scores the text). Every row is tagged `sentiment_source` (`stockdata` |
`vader`) and `n_scored_1/2/3`. The sheet stores **News Sentiment** (current) and
**News Change** (day-over-day).

If a ticker's news cells are empty the scan bootstraps from **3 days** so two
consecutive changes can be compared; later runs only need 1 day. The change
from day 2 -> day 3 (today) is the **meaningful move**; it is treated as
*significant* when it is larger in magnitude than the day 1 -> day 2 change
(noise filter). When a reliable comparison can't be built — only two days of
news exist, there's a **gap in news coverage** (a skipped day), or no stored
prior change — **any** move on the last day is treated as significant rather
than dropping the signal.

---

## Allocation & recommendations

- **Wealth** and base **Currency** are read from the top of the sheet.
- Wealth is split **equally** across the stocks that pass **both** the
  financial-health threshold **and** the positivity threshold (the "value" set).
- For each value stock the **Target** column shows target shares
  (`floor(allocated_value_in_currency / price)`).
- **Value Held** column shows `shares x price`.
- Edit the **Shares** column; the scan recommends:
  - `BUY n` when you're under target and the entry signal is good (price below
    its 21-day average and news not worsening),
  - `SELL n` when you're over target, or when a held stock loses value status
    and the exit signal aligns (price above average and news not improving).

## Spreadsheet layout (`Portfolio.xlsx`)

Rewritten each run. Above the data there is a small config / **Holdings
distribution** block: Wealth, Currency, the editable **Absolute Thresholds**
(Positivity, Z (single), Financial Health, News floor, Book-to-Market split
low/mid/high) followed by the share of holdings **by country** and **by
industry** (e.g. 70% held in France, 40% in biotech). Then the header:

`Company name | Tickers | Shares | Current Price | Currency | Country |
Industry | Z-Score | Positivity | Fundamentals | Book/Market | News
Sentiment | News Change | Target | Value Held | Avg Cost | Profit |
Recommended action | Important note(s)`

The watchlist is the rows below the header — add/remove tickers there. There
are **no asterisks**; anything the scan wants you to know (missing price /
incomplete fundamentals / restricted country / high withholding tax) is listed
as plain text in **Important note(s)**. **Fundamentals** is the plain financial
signal (0-1) and **Book/Market** the B/M used to blend it. **Avg Cost** updates
automatically: when you add shares it averages the new buy price with the
existing basis (using the last recorded price for the new shares). **Profit**
= `(price − avg cost) × shares`, shown in the share's currency (can be
negative). BUY rows shaded green, SELL red. On the first run the old layout is
auto-converted.

### Recommended thresholds (editable in the sheet)
| Threshold | Default | Rationale |
|---|---|---|
| Positivity | `0.55` | mild uptrend: > half the up-days over the window |
| Z (single) | `1.0` | BUY at z <= -1, SELL at z >= +1 (1 std-dev from mean) |
| Financial Health | `0.6` | >=60% of financial-health signals must pass |
| News floor | `0.2` | min day-change (0-10) to count as a real move |
| B/M split low / mid / high | `0.2` / `0.33` / `0.5` | pre->post logistic transition, 50/50 at 0.33 (P/B=3) |

## Install & run

```bash
./install.sh            # installs Python deps (venv or --user)
python3 clean_pass.py --write         # run a scan
```

## Restricted trades

`Restricted Trades.docx` lists countries/regions to avoid (with reasons). Each
run parses it and flags any watchlist stock whose **country** matches — e.g. a
Swiss name gets *"High IBKR tiered fees; ...; high withholding tax"* in the
**Important note(s)** column. This is purely informational: it does **not**
block a BUY/SELL decision.
For the few companies where a *more liquid* US listing exists it is preferred
(e.g. Agnico `AEM`, Wheaton `WPM`, Roche `RHHBY`); the US ADR's reported
country is still the home country, so those remain classed by domicile. A
plain-text `restricted_trades.txt` (one `TICKER` or `TICKER, reason` per line)
adds per-issuer flags.

Environment variables (all optional):
| Variable | Default | Meaning |
|---|---|---|
| `FETCH_DELAY` | `1.0` | seconds between all provider calls (fair use) |
| `STOCKDATA_KEYS` | `.env` | stockdata.org keys, comma-separated, failover on quota |
| `EULERPOOL_KEYS` | `.env` | eulerpool keys, comma-separated, failover on quota |
| `STOCKDATA_API_KEY` / `_2`, `EULERPOOL_API_KEY` | `.env` | legacy single-key aliases |

To run automatically, point your own `cron`/`systemd` timer at `clean_pass.py` — the
scan itself is on-demand.

## Setup

Copy `.env.example` to `.env` and fill in `STOCKDATA_API_KEY` and
`EULERPOOL_API_KEY` (gitignored). Put your `Restricted Trades.docx` in the
project folder; it too is gitignored and not pushed to GitHub.

## Files
- `clean_pass.py` — on-demand orchestrator (fetch -> score -> allocate -> write)
- `config.py` — defaults, thresholds, spreadsheet columns, source keys
- `sheet.py` — read/write `Portfolio.xlsx`
- `scores.py` — PRE/POST financial health + adaptive B/M blend + eulerpool KPI
- `statements.py` — extract income/balance/cash-flow series for the F-score
- `market.py` — yfinance fetch (price history, FX) 
- `eulerpool_fund.py` — eulerpool KPI (rich fundamentals)
- `news.py` — StockData news + entity sentiment, rolling 24h windows, Yahoo fallback
- `keys.py` — multi-key API failover (`call_with_failover`) + typed `AllKeysFailed`
- `restricted.py` — parse `Restricted Trades.docx` (countries) + txt overrides
- `sentiment.py` — VADER headline scorer (only the StockData fallback)
- `calculations.py` — Z-score, positivity, universe size-percentile weight
- `scorecard.py` — `S_unified` shadow scorecard (11 components, fixed thresholds)
- `build_calibration_set.py` — point-in-time (no-lookahead) forward-return calibration build
- `evaluate.py` — fit on forward returns + rank-IC / p-value report
- `fit_weights.py` — old theta/lambda fitter (incumbent proxy; superseded by evaluate.py)

> **Training-only Finnhub sentiment.** For building the calibration set, when
> StockData has zero articles for a ticker at T−20, `build_calibration_set.py`
> falls back to Finnhub `/company-news` scored with VADER into the same rolling
> 24h windows and tags `sentiment_source=finnhub`. **Finnhub is NEVER wired into
> `clean_pass.py` / the live scan** — training data only. Shortage of historical
> sentiment is tagged `sentiment_unavailable_historical`, never fabricated.

## Data sources & ticker formats

Three sources are combined, each throttled at 1 s per call (`FETCH_DELAY`):
- **News**: stockdata.org (global; ~100 calls/day free tier — kept as the
  first try, with a reliable Yahoo fallback) — no Finnhub.
- **Prices / FX / fallback fundamentals**: yfinance.
- **Fundamentals**: eulerpool KPI (revenue, growth, margins, ROA/ROE, liquidity,
  leverage, Piotroski, Altman — far richer than yfinance `info`) used as the
  robust financial signal, with yfinance fallback.

Tickers are largely interchangeable across these sources (eulerpool accepts the
Yahoo-style `LONN.SW`, `CS.PA`, `2171.HK`). Where they differ, a per-source
alias applies — e.g. Roche is `RHHBY` on yfinance but `ROG.SW` on eulerpool
(`EULER_TICKER_MAP`).

## Caveats
- The program only *recommends*; it does not place trades.
- News is sparse for small biotechs on any day — the filter, 3-day bootstrap
  and empty-handling account for this.
- Prices and FX use quotes at scan time; treated as current.
- Restricted detection keys on the stock's reported **country**; a stock that
  moves its listing/domicile changes classification on the next scan.
