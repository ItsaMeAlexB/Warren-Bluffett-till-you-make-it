#!/usr/bin/env python3
"""keys.py - multi-key API failover + tagged failure.

Callers wrap their requests in call_with_failover(keys, request_fn, endpoint).
On quota/auth errors (401/402/403/429) we move to the next key; on any other
HTTP error we raise (parameter or server bug, not quota). If every key fails
with a quota/auth error it raises AllKeysFailed - callers MUST fall back to
yfinance and TAG the source (e.g. 'stockdata_all_keys_failed' /
'eulerpool_all_keys_failed'), never return empty data silently.

Key lists come from comma-separated env vars STOCKDATA_KEYS / EULERPOOL_KEYS
(1..N keys), falling back to the legacy single-key vars for backward compat.
"""
import logging
import os

logger = logging.getLogger(__name__)

QUOTA_HTTP = (401, 402, 403, 429)


class AllKeysFailed(Exception):
    """Every configured key failed with a quota/auth error for `endpoint`."""

    def __init__(self, endpoint):
        self.endpoint = endpoint
        super().__init__(f"all keys failed (quota/auth) for {endpoint}")


def _split(raw):
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def stockdata_keys():
    """Comma-separated STOCKDATA_KEYS if set, else legacy single-key vars."""
    if os.environ.get("STOCKDATA_KEYS"):
        return _split(os.environ["STOCKDATA_KEYS"])
    return [v for v in (os.environ.get("STOCKDATA_API_KEY", ""),
                        os.environ.get("STOCKDATA_API_KEY_2", "")) if v]


def eulerpool_keys():
    """Comma-separated EULERPOOL_KEYS if set, else the legacy single-key var."""
    if os.environ.get("EULERPOOL_KEYS"):
        return _split(os.environ["EULERPOOL_KEYS"])
    v = os.environ.get("EULERPOOL_API_KEY", "")
    return [v] if v else []


def finnhub_keys():
    """Comma-separated FINNHUB_KEYS if set, else the legacy single-key var."""
    if os.environ.get("FINNHUB_KEYS"):
        return _split(os.environ["FINNHUB_KEYS"])
    v = os.environ.get("FINNHUB_KEY", "")
    return [v] if v else []


def _api_error(r):
    try:
        j = r.json()
        if isinstance(j, dict):
            e = j.get("error") or {}
            return e.get("code") or e.get("message") or ""
    except Exception:
        pass
    return ""


def call_with_failover(keys, request_fn, endpoint, ticker=""):
    """Run request_fn(key) -> requests.Response over `keys`.

    Returns (response, key_index_used). On 401/402/403/429 moves to the next key;
    on any other non-200 raises RuntimeError (param/server bug, not quota); on
    every key failing with a quota/auth error raises AllKeysFailed. Logs one line
    per attempt: key_index, http status, api error, endpoint.
    """
    for i, key in enumerate(keys):
        try:
            r = request_fn(key)
        except Exception as e:
            logger.error("key %d ticker=%s endpoint=%s request exception: %s",
                         i, ticker, endpoint, e)
            raise
        err = _api_error(r)
        logger.info("key %d ticker=%s endpoint=%s http=%s api_error=%s",
                    i, ticker, endpoint, r.status_code, err)
        if r.status_code == 200:
            return r, i
        if r.status_code in QUOTA_HTTP:
            continue
        raise RuntimeError(f"{endpoint} returned HTTP {r.status_code}"
                           f" (api_error={err}) - not a quota error")
    raise AllKeysFailed(endpoint)
