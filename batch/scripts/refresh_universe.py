#!/usr/bin/env python3
"""Refresh the bundled US universe lists from Wikipedia (manual helper).

Usage (from the ``batch/`` directory)::

    python scripts/refresh_universe.py

Rewrites ``compass/data/sp500.txt`` and ``compass/data/nasdaq100.txt``
from the Wikipedia membership tables.  Run occasionally by hand — the
lists don't need to be perfect, the batch just scans whatever is there.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parents[1] / "compass" / "data"

SOURCES = {
    "sp500.txt": (
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "S&P 500 membership (refreshed from Wikipedia)",
    ),
    "nasdaq100.txt": (
        "https://en.wikipedia.org/wiki/Nasdaq-100",
        "Nasdaq-100 membership (refreshed from Wikipedia)",
    ),
}

# Wikipedia constituent tables link tickers to quote pages; both list
# pages carry rows like:  <td><a ... href="...">TICKER</a>  where the
# link target is a stockanalysis/nyse/nasdaq quote URL.
TICKER_RE = re.compile(
    r'href="https?://(?:www\.)?(?:nasdaq\.com/market-activity/stocks/|'
    r'nyse\.com/quote/\w+:|stockanalysis\.com/stocks/)'
    r'([A-Za-z.\-]{1,7})[/"]',
)


def extract_tickers(html: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in TICKER_RE.finditer(html):
        ticker = match.group(1).upper().replace("-", ".")
        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)
    return out


def main() -> int:
    for filename, (url, header) in SOURCES.items():
        print(f"fetching {url} ...")
        resp = requests.get(url, timeout=60,
                            headers={"User-Agent": "stock-compass-batch/0.1"})
        resp.raise_for_status()
        tickers = extract_tickers(resp.text)
        if len(tickers) < 50:
            print(f"  only {len(tickers)} tickers parsed — page layout may have "
                  f"changed; keeping existing {filename}", file=sys.stderr)
            continue
        path = DATA_DIR / filename
        lines = [f"# {header}",
                 '# One ticker per line. "." class notation kept as-is; '
                 "converted for yfinance in code."]
        lines += tickers
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  wrote {len(tickers)} tickers -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
