"""Cross-sectional logic: true RS Rating percentiles, Magic Formula
double ranking, market breadth, and per-model top-5 selection.

This module is the whole point of running an external batch — Pine
scripts cannot see other symbols, so real percentiles/rankings can only
happen here (DESIGN.md section 1).
"""

from __future__ import annotations

import math

import pandas as pd

from .models import TRADING_DAYS_6M, close_series


def six_month_returns(prices: dict[str, pd.DataFrame]) -> dict[str, float]:
    """126-trading-day return per ticker (skips histories that are too short)."""
    out: dict[str, float] = {}
    for ticker, df in prices.items():
        close = close_series(df)
        if close is None or len(close) <= TRADING_DAYS_6M:
            continue
        base = float(close.iloc[-1 - TRADING_DAYS_6M])
        if base > 0:
            out[ticker] = float(close.iloc[-1]) / base - 1.0
    return out


def rs_percentiles(returns: dict[str, float]) -> dict[str, float]:
    """True RS Rating: percentile (0-100) of each 6-month return across
    the whole scanned universe. >= 70 passes CANSLIM C5 / Minervini T8."""
    if not returns:
        return {}
    series = pd.Series(returns)
    ranks = series.rank(pct=True) * 100.0
    return {t: float(v) for t, v in ranks.items()}


def breadth_above_200ma(prices: dict[str, pd.DataFrame]) -> float | None:
    """% of the scanned universe trading above its own 200-day MA.

    This is the batch's advantage for the TW thermometer: TradingView has
    no breadth symbol for Taiwan, so we compute it from the full scan.
    """
    above = total = 0
    for df in prices.values():
        close = close_series(df)
        if close is None or len(close) < 200:
            continue
        total += 1
        if float(close.iloc[-1]) > float(close.iloc[-200:].mean()):
            above += 1
    if total == 0:
        return None
    return 100.0 * above / total


def magic_formula_rank(greenblatt_results: list[dict]) -> list[dict]:
    """Greenblatt Magic Formula double ranking.

    Rank all symbols by earnings yield (descending: highest EY = rank 1)
    and separately by ROIC (descending), sum the two ranks, then sort by
    the combined rank ascending — the classic Magic Formula ordering.
    Symbols missing either metric are excluded.  Each returned result
    gains ``metrics["magic_rank"]`` (1 = best).
    """
    eligible = [r for r in greenblatt_results
                if r.get("earnings_yield") is not None and r.get("roic") is not None]
    if not eligible:
        return []
    by_ey = sorted(eligible, key=lambda r: r["earnings_yield"], reverse=True)
    by_roic = sorted(eligible, key=lambda r: r["roic"], reverse=True)
    ey_rank = {r["symbol"]: i + 1 for i, r in enumerate(by_ey)}
    roic_rank = {r["symbol"]: i + 1 for i, r in enumerate(by_roic)}
    for r in eligible:
        r["metrics"]["combined_rank"] = ey_rank[r["symbol"]] + roic_rank[r["symbol"]]
    ordered = sorted(eligible, key=lambda r: r["metrics"]["combined_rank"])
    for i, r in enumerate(ordered):
        r["metrics"]["magic_rank"] = i + 1
    return ordered


# Per-model tiebreak for top-5 selection: primary sort is pct descending,
# ties broken by a model-relevant secondary metric:
#   graham    -> lowest P/E×P/B (cheapest defensive)
#   buffett   -> highest ROE (strongest moat proxy)
#   lynch     -> lowest PEG (most growth per valuation unit)
#   canslim   -> highest RS Rating percentile
#   minervini -> highest 6-month RS percentile
#   schloss   -> lowest P/B (deepest value)
#   templeton -> deepest drawdown (most pessimism)
# Missing metrics sort last within their pct group.
_TIEBREAK: dict[str, tuple[str, bool]] = {  # metric key -> (name, higher_is_better)
    "graham": ("pe_pb", False),
    "buffett": ("roe", True),
    "lynch": ("peg", False),
    "canslim": ("rs", True),
    "minervini": ("rs", True),
    "schloss": ("pb", False),
    "templeton": ("drawdown", False),
}


def top5(results: list[dict], model: str, n: int = 5) -> list[dict]:
    """Top-N for one model.

    greenblatt is special-cased: its ranking IS the Magic Formula
    combined rank (DESIGN.md 3.4 批次版), not the 2-criterion pct.
    """
    if model == "greenblatt":
        return magic_formula_rank(results)[:n]

    metric, higher_better = _TIEBREAK[model]

    def sort_key(r: dict) -> tuple:
        value = r["metrics"].get(metric)
        if value is None:
            tiebreak = math.inf  # metric-less rows sort last in their group
        else:
            tiebreak = -value if higher_better else value
        return (-r["pct"], tiebreak, r["symbol"])

    return sorted(results, key=sort_key)[:n]
