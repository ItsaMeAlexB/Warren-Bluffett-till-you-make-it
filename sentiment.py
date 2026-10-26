#!/usr/bin/env python3
"""sentiment.py - headline scorer (VADER).

VADER is the scorer (lightweight, no extra deps). No ML/vendor model is used:
StockData's news endpoint returns no sentiment field and has no /sentiment
endpoint, and yfinance exposes news but no sentiment score, so every headline
is scored locally with VADER. Every scored row is therefore flagged
sentiment_source="vader" upstream.

score(text) -> float in [-1, 1]; negatives are bad.
"""
import logging

logger = logging.getLogger(__name__)
_VADER_ANALYZER = None

# The only sentiment signal available on this setup is local VADER (StockData
# exposes no sentiment signal, yfinance has none), so the source never varies
# unless a real vendor/ML scorer is wired in.
SOURCE = "vader"


def _vader():
    global _VADER_ANALYZER
    if _VADER_ANALYZER is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _VADER_ANALYZER = SentimentIntensityAnalyzer()
    return _VADER_ANALYZER


def score(text):
    """Return compound sentiment in [-1, 1] for a headline."""
    if not text:
        return 0.0
    return _vader().polarity_scores(text)["compound"]
