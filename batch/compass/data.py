"""yfinance data adapter with on-disk caching.

Prices: batch download of ~3 years of daily bars via ``yf.download``
(chunked, threaded, retried with backoff).  Index history is fetched
with a longer window because the thermometer needs 5 years of context.

Fundamentals: per-symbol ``Ticker.info`` + annual/quarterly statements.
Every access is wrapped; anything missing becomes ``None`` so the
scorers can record it as 資料缺漏 (data unavailable = criterion fails
but is tracked separately).

Cache: pickles under ``batch/.cache/<date>/`` so re-runs on the same
day are cheap.
"""

from __future__ import annotations

import logging
import pickle
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

BATCH_DIR = Path(__file__).resolve().parents[1]
CACHE_ROOT = BATCH_DIR / ".cache"

PRICE_PERIOD = "3y"
INDEX_PERIOD = "6y"   # thermometer percentiles need 5y of history
CHUNK_SIZE = 100
MAX_RETRIES = 3
BACKOFF_SECONDS = 5.0


@dataclass
class Fundamentals:
    """Best-effort fundamental data for one symbol (all fields optional)."""

    info: dict = field(default_factory=dict)
    income: pd.DataFrame | None = None       # annual income statement
    income_q: pd.DataFrame | None = None     # quarterly income statement
    balance: pd.DataFrame | None = None      # annual balance sheet
    balance_q: pd.DataFrame | None = None    # quarterly balance sheet
    cashflow: pd.DataFrame | None = None     # annual cash flow
    cashflow_q: pd.DataFrame | None = None   # quarterly cash flow
    dividends: pd.Series | None = None       # full dividend history


def _cache_dir(date_key: str) -> Path:
    d = CACHE_ROOT / date_key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker)


def _cache_load(path: Path) -> Any | None:
    if path.exists():
        try:
            with path.open("rb") as fh:
                return pickle.load(fh)
        except Exception as exc:  # noqa: BLE001 - corrupt cache is not fatal
            log.warning("cache read failed for %s: %s", path, exc)
    return None


def _cache_save(path: Path, obj: Any) -> None:
    try:
        with path.open("wb") as fh:
            pickle.dump(obj, fh)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache write failed for %s: %s", path, exc)


def _normalize_price_frame(df: pd.DataFrame) -> pd.DataFrame | None:
    """Keep Close/High/Low/Volume, drop all-NaN rows; None if unusable."""
    if df is None or df.empty:
        return None
    cols = [c for c in ("Close", "High", "Low", "Volume") if c in df.columns]
    if "Close" not in cols:
        return None
    out = df[cols].dropna(subset=["Close"])
    if out.empty:
        return None
    return out


def _download_chunk(tickers: list[str], period: str) -> dict[str, pd.DataFrame]:
    """One yf.download call with retry/backoff; returns per-ticker frames."""
    import yfinance as yf

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = yf.download(
                tickers=tickers,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = BACKOFF_SECONDS * attempt
            log.warning("yf.download attempt %d/%d failed (%s); retrying in %.0fs",
                        attempt, MAX_RETRIES, exc, wait)
            time.sleep(wait)
    else:
        log.error("yf.download gave up on chunk of %d tickers: %s", len(tickers), last_exc)
        return {}

    result: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return result
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            if t in raw.columns.get_level_values(0):
                frame = _normalize_price_frame(raw[t])
                if frame is not None:
                    result[t] = frame
    else:  # single ticker: flat columns
        frame = _normalize_price_frame(raw)
        if frame is not None:
            result[tickers[0]] = frame
    return result


def download_prices(tickers: list[str], date_key: str, market: str,
                    period: str = PRICE_PERIOD) -> dict[str, pd.DataFrame]:
    """Batch-download daily price history for ``tickers``, with caching."""
    cache_path = _cache_dir(date_key) / f"prices_{market}_{period}.pkl"
    cached = _cache_load(cache_path)
    if isinstance(cached, dict):
        missing = [t for t in tickers if t not in cached]
        if not missing:
            log.info("prices for %s loaded from cache (%d tickers)", market, len(tickers))
            return {t: cached[t] for t in tickers}

    prices: dict[str, pd.DataFrame] = dict(cached) if isinstance(cached, dict) else {}
    todo = [t for t in tickers if t not in prices]
    for i in range(0, len(todo), CHUNK_SIZE):
        chunk = todo[i:i + CHUNK_SIZE]
        log.info("downloading prices %d-%d / %d", i + 1, i + len(chunk), len(todo))
        prices.update(_download_chunk(chunk, period))
    _cache_save(cache_path, prices)
    return {t: prices[t] for t in tickers if t in prices}


def download_index(ticker: str, date_key: str) -> pd.DataFrame | None:
    """Download index history (longer window for the thermometer)."""
    cache_path = _cache_dir(date_key) / f"index_{_safe_name(ticker)}.pkl"
    cached = _cache_load(cache_path)
    if isinstance(cached, pd.DataFrame) and not cached.empty:
        return cached
    frames = _download_chunk([ticker], INDEX_PERIOD)
    frame = frames.get(ticker)
    if frame is not None:
        _cache_save(cache_path, frame)
    return frame


def _safe_df(getter) -> pd.DataFrame | None:
    try:
        df = getter()
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except Exception:  # noqa: BLE001 - yfinance raises freely; missing -> None
        pass
    return None


def fetch_fundamentals(ticker: str, date_key: str) -> Fundamentals:
    """Fetch (or load cached) fundamentals for one symbol; never raises."""
    cache_path = _cache_dir(date_key) / f"fund_{_safe_name(ticker)}.pkl"
    cached = _cache_load(cache_path)
    if isinstance(cached, Fundamentals):
        return cached

    import yfinance as yf

    fund = Fundamentals()
    try:
        t = yf.Ticker(ticker)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ticker(%s) failed: %s", ticker, exc)
        return fund

    try:
        info = t.info
        if isinstance(info, dict):
            fund.info = info
    except Exception as exc:  # noqa: BLE001
        log.debug("%s .info failed: %s", ticker, exc)

    fund.income = _safe_df(lambda: t.income_stmt)
    fund.income_q = _safe_df(lambda: t.quarterly_income_stmt)
    fund.balance = _safe_df(lambda: t.balance_sheet)
    fund.balance_q = _safe_df(lambda: t.quarterly_balance_sheet)
    fund.cashflow = _safe_df(lambda: t.cashflow)
    fund.cashflow_q = _safe_df(lambda: t.quarterly_cashflow)
    try:
        div = t.dividends
        # Keep even an empty series: empty = "no dividends ever paid",
        # None = "could not fetch" (資料缺漏).
        if isinstance(div, pd.Series):
            fund.dividends = div
    except Exception:  # noqa: BLE001
        pass

    _cache_save(cache_path, fund)
    return fund


# ---------------------------------------------------------------------------
# Safe accessors shared by the scorers
# ---------------------------------------------------------------------------

def num(value: Any) -> float | None:
    """Coerce to a finite float, else None."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def info_num(fund: Fundamentals, key: str) -> float | None:
    """Numeric field from Ticker.info, missing/NaN -> None."""
    return num(fund.info.get(key))


def stmt_row(df: pd.DataFrame | None, *labels: str) -> pd.Series | None:
    """First matching row from a statement DataFrame.

    yfinance statements have line items as the index and period end
    dates as columns ordered newest-first; NaN cells are dropped.
    """
    if df is None:
        return None
    for label in labels:
        if label in df.index:
            series = df.loc[label].dropna()
            if isinstance(series, pd.DataFrame):  # duplicated label
                series = series.iloc[0].dropna()
            if not series.empty:
                return series
    return None


def annual_values(df: pd.DataFrame | None, *labels: str) -> list[float] | None:
    """Row values newest-first as floats, or None if unavailable."""
    series = stmt_row(df, *labels)
    if series is None:
        return None
    values = [num(v) for v in series.tolist()]
    values = [v for v in values if v is not None]
    return values or None
