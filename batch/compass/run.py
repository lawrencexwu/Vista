"""CLI entry point for the daily batch.

Usage (from the ``batch/`` directory)::

    python -m compass.run --market tw
    python -m compass.run --market us --limit 15          # smoke test
    python -m compass.run --market us --date 2026-07-08   # explicit cache key

Individual symbol failures are logged, skipped and counted — one bad
ticker never kills the batch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

from . import MODELS
from .data import download_index, download_prices, fetch_fundamentals
from .models import MarketContext, close_series, score_all
from .rank import breadth_above_200ma, rs_percentiles, six_month_returns, top5
from .report import write_report
from .thermometer import compute_thermometer
from .universe import INDEX_NAME_ZH, INDEX_TICKER, get_universe

log = logging.getLogger("compass")

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = REPO_ROOT / "reports"

PROGRESS_EVERY = 25


def run(market: str, limit: int | None, date_key: str) -> Path:
    """Run the full pipeline for one market; returns the report path."""
    symbols = get_universe(market, limit)
    log.info("universe: %d symbols (%s)", len(symbols), market)

    tickers = [s.yf_ticker for s in symbols]
    prices = download_prices(tickers, date_key, market)
    log.info("price history available for %d/%d symbols", len(prices), len(tickers))

    index_ticker = INDEX_TICKER[market]
    index_prices = download_index(index_ticker, date_key)
    if index_prices is None:
        log.warning("index download failed for %s — thermometer will be partial", index_ticker)

    ctx = MarketContext(
        market=market,
        index_close=close_series(index_prices),
        rs_percentile=rs_percentiles(six_month_returns(prices)),
    )
    breadth = breadth_above_200ma(prices)

    results_by_model: dict[str, list[dict]] = {m: [] for m in MODELS}
    exchange_hints: dict[str, str | None] = {}
    scored = skipped = 0
    for i, sym in enumerate(symbols, 1):
        if i % PROGRESS_EVERY == 0 or i == len(symbols):
            log.info("scoring %d/%d (%s)", i, len(symbols), sym.yf_ticker)
        sym_prices = prices.get(sym.yf_ticker)
        if sym_prices is None:
            skipped += 1
            continue
        try:
            fund = fetch_fundamentals(sym.yf_ticker, date_key)
            exchange_hints[sym.yf_ticker] = fund.info.get("exchange")
            if sym.market == "us":
                long_name = fund.info.get("shortName") or fund.info.get("longName")
                if long_name:
                    sym.name = str(long_name)
            for result in score_all(sym, sym_prices, fund, ctx):
                results_by_model[result["model"]].append(result)
            scored += 1
        except Exception:  # noqa: BLE001 - tolerate individual symbol failures
            log.exception("scoring failed for %s — skipped", sym.yf_ticker)
            skipped += 1

    tops = {m: top5(results_by_model[m], m) for m in MODELS}
    thermo = compute_thermometer(index_prices, breadth)

    stats = {
        "universe": len(symbols),
        "scored": scored,
        "skipped": skipped,
    }
    if scored == 0:
        stats["note"] = "本次執行無法取得任何價格資料（yfinance 可能無法連線），請檢查網路。"

    return write_report(
        market=market,
        date_key=date_key,
        thermo=thermo,
        index_name=f"{INDEX_NAME_ZH[market]}（{index_ticker}）",
        results_by_model=results_by_model,
        tops=tops,
        stats=stats,
        exchange_hints=exchange_hints,
        reports_root=REPORTS_ROOT,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m compass.run",
        description="選股羅盤 daily batch: score TW/US universes against "
                    "the eight master models and write daily reports.",
    )
    parser.add_argument("--market", choices=["tw", "us"], required=True)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap universe size (smoke tests)")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="report/cache date key (default: today UTC)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    date_key = args.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    try:
        dt.date.fromisoformat(date_key)
    except ValueError:
        parser.error(f"--date must be YYYY-MM-DD, got {date_key!r}")

    report_path = run(args.market, args.limit, date_key)
    log.info("done: %s", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
