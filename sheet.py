#!/usr/bin/env python3
"""sheet.py - read/write Portfolio.xlsx (openpyxl).

Keeps a config/threshold header block, then a stock table. The old
Google-Sheets-style layout (header row with fewer columns) is auto-detected so
the first run converts it in place. Previous News Sentiment / Change and user
share holdings are preserved so the scan can build on them.
"""
import openpyxl
from openpyxl.styles import PatternFill

from config import XLSX_PATH, DATA_HEADER_ROW, COLS

GREEN = PatternFill("solid", fgColor="C6EFCE")
RED = PatternFill("solid", fgColor="FFC7CE")
ORANGE = PatternFill("solid", fgColor="FFC000")


def load_ws():
    wb = openpyxl.load_workbook(XLSX_PATH)
    return wb, wb["Sheet1"]


def find_header_row(ws):
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, 1).value == "Company name":
            return r
    return None


def read_config(ws, defaults):
    cfg = dict(defaults)
    hr = find_header_row(ws) or DATA_HEADER_ROW
    for row in ws.iter_rows(min_row=1, max_row=hr - 1, max_col=3,
                            values_only=True):
        key = row[0]
        if not isinstance(key, str):
            continue
        k = key.strip().lower()
        v1, v2 = row[1], row[2]
        if k in ("wealth", "positivity", "financial health", "news floor",
                 "book-to-market threshold", "book-to-market split mid",
                 "z price", "z-score", "z score"):
            if v1 is None:
                continue
            if k == "wealth":
                cfg["wealth"] = float(v1) if isinstance(v1, (int, float)) \
                    else float(str(v1).replace(",", ""))
            elif k == "positivity":
                cfg["positivity"] = float(v1)
            elif k == "financial health":
                cfg["fin_health"] = float(v1)
            elif k == "news floor":
                cfg["news_floor"] = float(v1)
            elif k in ("book-to-market threshold", "book-to-market split mid"):
                cfg["btm_threshold"] = float(v1)
            elif k in ("z", "z price", "z-score", "z score"):
                cfg["z"] = float(v1)
        elif k == "currency" and v1 is not None:
            cfg["currency"] = str(v1).strip()
    return cfg


def read_portfolio(ws):
    hr = find_header_row(ws)
    if hr is None:
        return []
    has_news = (ws.cell(hr, COLS["news_sentiment"] + 1).value
                == "News Sentiment")
    rows = []
    for row in ws.iter_rows(min_row=hr + 1, values_only=True):
        if not row or not row[COLS["ticker"]]:
            continue
        def gv(idx):
            v = row[idx] if idx < len(row) else None
            return v
        try:
            shares = float(gv(COLS["shares"]) or 0.0)
        except (TypeError, ValueError):
            shares = 0.0
        rows.append({
            "ticker": str(gv(COLS["ticker"])).strip(),
            "euler_ticker": str(gv(COLS["euler_ticker"]) or "").strip(),
            "sd_ticker": str(gv(COLS["sd_ticker"]) or "").strip(),
            "company": gv(COLS["company"]),
            "shares": shares,
            "avg_cost": gv(COLS["avg_cost"]),
            "prev_shares": gv(COLS["prev_shares"]),
            "prev_price": gv(COLS["prev_price"]),
            "news_sentiment": gv(COLS["news_sentiment"]) if has_news else None,
            "news_change": gv(COLS["news_change"]) if has_news else None,
        })
    return rows


def write_sheet(ws, cfg, results):
    import sys as _sys
    _sys.exit("Use clean_pass.py — config.COLS is stale and would corrupt the new layout.")
    from openpyxl.utils import get_column_letter
    ws.delete_rows(1, ws.max_row)
    # config header
    ws.cell(1, 1, "Wealth"); ws.cell(1, 2, cfg["wealth"])
    ws.cell(2, 1, "Currency"); ws.cell(2, 2, cfg["currency"])
    ws.cell(4, 1, "Absolute Thresholds"); ws.cell(4, 2, "Value")
    ws.cell(5, 1, "Positivity"); ws.cell(5, 2, cfg["positivity"])
    ws.cell(6, 1, "Z price"); ws.cell(6, 2, cfg["z"])
    ws.cell(7, 1, "Financial Health"); ws.cell(7, 2, cfg["fin_health"])
    ws.cell(8, 1, "News floor"); ws.cell(8, 2, cfg["news_floor"])
    ws.cell(9, 1, "Book-to-Market threshold")
    ws.cell(9, 2, cfg.get("btm_threshold", cfg.get("btm_threshold", 0.33)))

    # holdings distribution by country and by industry (value in base currency)
    byc, byi = {}, {}
    for r in results:
        v = r.get("value_base") or 0.0
        if v <= 0:
            continue
        c = (r.get("country") or "Unknown").strip() or "Unknown"
        i = (r.get("industry") or "Unknown").strip() or "Unknown"
        byc[c] = byc.get(c, 0.0) + v
        byi[i] = byi.get(i, 0.0) + v

    row = 12
    ws.cell(row, 1, "Holdings distribution")
    tot = sum(byc.values()) or 1.0
    row += 1
    for lab, v in sorted(byc.items(), key=lambda kv: -kv[1]):
        ws.cell(row, 1, lab); ws.cell(row, 2, round(100.0 * v / tot, 1)); row += 1
    row += 1
    ws.cell(row, 1, "Industry"); ws.cell(row, 2, "% of holdings"); row += 1
    tot = sum(byi.values()) or 1.0
    for lab, v in sorted(byi.items(), key=lambda kv: -kv[1]):
        ws.cell(row, 1, lab); ws.cell(row, 2, round(100.0 * v / tot, 1)); row += 1
    row += 1
    hdr_row = row

    hdr = ["Company name", "Tickers", "Shares", "Current Price", "Currency",
           "Country", "Industry", "Z-Score", "Positivity", "Fundamentals",
           "Book/Market", "News Sentiment", "News Change", "Target",
           "Value Held", "Avg Cost", "Profit", "Prev Shares", "Prev Price",
           "Recommended action", "Important note(s)", "Euler Ticker",
           "Stockdata Ticker", "Size", "S_unified (shadow)"]
    for c, h in enumerate(hdr):
        ws.cell(hdr_row, c + 1, h)

    row_i = hdr_row + 1
    for r in results:
        vals = [r.get(k) for k in
                ("company", "ticker", "shares", "price", "currency", "country",
                 "industry", "zscore", "positivity", "fundamentals", "bm",
                 "news_sentiment", "news_change", "target", "value_held",
                 "avg_cost", "profit", "prev_shares", "prev_price", "action",
                 "reasons", "euler_ticker", "sd_ticker", "size", "s_unified")]
        for c, v in enumerate(vals):
            ws.cell(row_i, c + 1, v)
        if r.get("_warn"):                       # a source fell back -> orange
            ws.cell(row_i, COLS["price"] + 1).fill = ORANGE
        act_cell = ws.cell(row_i, COLS["action"] + 1)
        act = r.get("action", "")
        if isinstance(act, str) and act.startswith("BUY"):
            act_cell.fill = GREEN
        elif isinstance(act, str) and act.startswith("SELL"):
            act_cell.fill = RED
        row_i += 1
    # internal tracking columns are hidden (maintained by the scan)
    for key in ("prev_shares", "prev_price"):
        ws.column_dimensions[get_column_letter(COLS[key] + 1)].hidden = True
    ws.parent.save(XLSX_PATH)
