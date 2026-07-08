"""ETF 專區（向柏格致敬）: trend snapshot for a curated ETF list.

Bogle's point is the baseline: every active pick competes against
"just buy the haystack".  This section gives that haystack a daily
health check — price trend, returns and range position — with no
scoring, no ranking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .data import download_prices

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass
class EtfInfo:
    yf_ticker: str
    tv_symbol: str
    name: str


def load_etf_list(market: str) -> list[EtfInfo]:
    """Parse ``data/etf_<market>.txt`` (``yf|tv|name`` per line)."""
    path = DATA_DIR / f"etf_{market}.txt"
    etfs: list[EtfInfo] = []
    if not path.exists():
        return etfs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 3 and all(parts):
            etfs.append(EtfInfo(*parts))
    return etfs


def etf_snapshot(prices: pd.DataFrame) -> dict | None:
    """Trend snapshot from a daily price frame; None if history too short."""
    close = prices["Close"].dropna()
    if len(close) < 60:
        return None
    last = float(close.iloc[-1])

    def ret_pct(n: int) -> float | None:
        if len(close) <= n:
            return None
        base = float(close.iloc[-n - 1])
        return (last / base - 1) * 100 if base > 0 else None

    dist_200ma = None
    if len(close) >= 200:
        sma200 = float(close.rolling(200).mean().iloc[-1])
        if sma200 > 0:
            dist_200ma = (last / sma200 - 1) * 100

    window = close.tail(252)
    hi, lo = float(window.max()), float(window.min())
    range_pos = (last - lo) / (hi - lo) * 100 if hi > lo else None

    return {
        "close": last,
        "ret_6m": ret_pct(126),
        "ret_1y": ret_pct(252),
        "dist_200ma": dist_200ma,
        "range_pos": range_pos,
    }


def compute_etf_rows(market: str, date_key: str) -> list[dict]:
    """Download and snapshot the market's curated ETF list.

    Rows without price data keep only tv/name so the report can show
    the gap instead of silently dropping the ETF.
    """
    etfs = load_etf_list(market)
    if not etfs:
        return []
    prices = download_prices([e.yf_ticker for e in etfs], date_key, f"{market}_etf")
    rows: list[dict] = []
    for e in etfs:
        frame = prices.get(e.yf_ticker)
        snap = etf_snapshot(frame) if frame is not None else None
        rows.append({"tv": e.tv_symbol, "name": e.name, **(snap or {})})
    return rows
