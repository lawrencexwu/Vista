"""The eight master-model scorers (DESIGN.md section 3).

Every scorer returns::

    {model, symbol, code, name, board,
     passed: [criterion ids], failed: [...], na: [...],
     score: passed_count, total, pct,
     metrics: {...}, key_metric: "display string"}

Semantics: each criterion evaluates to True / False / None.
``None`` means the required data is unavailable — it does NOT count as
passed (missing data = fail for scoring purposes) but is tracked in the
separate ``na`` list so the report can show data availability
(資料缺漏) instead of mislabelling a company as bad.
``pct = score / total``, i.e. na criteria drag the percentage down.

Price-based criteria use the downloaded ~3y daily history; fundamental
criteria use yfinance statements (annual history, as many years as
available — yfinance typically exposes ~4 fiscal years).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import pandas as pd

from .data import Fundamentals, annual_values, info_num, num, stmt_row
from .universe import SymbolInfo

TRADING_DAYS_6M = 126
TRADING_DAYS_13W = 65
TRADING_DAYS_1Y = 252


@dataclass
class MarketContext:
    """Cross-sectional context shared by all scorers."""

    market: str                                   # "tw" | "us"
    index_close: pd.Series | None = None          # benchmark index close
    rs_percentile: dict[str, float] = field(default_factory=dict)  # ticker -> 0-100


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def close_series(prices: pd.DataFrame | None) -> pd.Series | None:
    """Clean close series with a tz-naive index, or None."""
    if prices is None or "Close" not in prices.columns:
        return None
    close = prices["Close"].dropna()
    if close.empty:
        return None
    if getattr(close.index, "tz", None) is not None:
        close = close.copy()
        close.index = close.index.tz_localize(None)
    return close


def _sma(series: pd.Series, n: int) -> float | None:
    if len(series) < n:
        return None
    return float(series.iloc[-n:].mean())


def _sma_ago(series: pd.Series, n: int, ago: int) -> float | None:
    """SMA(n) as of ``ago`` bars back."""
    if len(series) < n + ago:
        return None
    return float(series.iloc[-(n + ago):-ago].mean())


def _return(series: pd.Series, n: int) -> float | None:
    if len(series) <= n:
        return None
    base = float(series.iloc[-1 - n])
    if base == 0:
        return None
    return float(series.iloc[-1]) / base - 1.0


def _window(series: pd.Series, n: int) -> pd.Series | None:
    if len(series) < n:
        return None
    return series.iloc[-n:]


def _bal_latest(fund: Fundamentals, *labels: str) -> float | None:
    """Latest value of a balance-sheet row (quarterly first, then annual)."""
    for df in (fund.balance_q, fund.balance):
        row = stmt_row(df, *labels)
        if row is not None:
            return num(row.iloc[0])
    return None


def _aligned_rows(df: pd.DataFrame | None, labels_a: tuple[str, ...],
                  labels_b: tuple[str, ...]) -> list[tuple[float, float]] | None:
    """(a, b) pairs newest-first for columns where both rows are present."""
    row_a = stmt_row(df, *labels_a)
    row_b = stmt_row(df, *labels_b)
    if row_a is None or row_b is None:
        return None
    pairs = []
    for col in row_a.index:
        if col in row_b.index:
            a, b = num(row_a[col]), num(row_b[col])
            if a is not None and b is not None:
                pairs.append((a, b))
    return pairs or None


def _debt_to_equity(fund: Fundamentals) -> float | None:
    """Debt/equity as a ratio (yfinance info reports it in percent)."""
    de = info_num(fund, "debtToEquity")
    if de is not None:
        return de / 100.0
    debt = _bal_latest(fund, "Total Debt")
    equity = _bal_latest(fund, "Stockholders Equity", "Total Equity Gross Minority Interest")
    if debt is not None and equity is not None and equity > 0:
        return debt / equity
    return None


def _current_ratio(fund: Fundamentals) -> float | None:
    cr = info_num(fund, "currentRatio")
    if cr is not None:
        return cr
    ca = _bal_latest(fund, "Current Assets")
    cl = _bal_latest(fund, "Current Liabilities")
    if ca is not None and cl is not None and cl > 0:
        return ca / cl
    return None


def _trailing_pe(fund: Fundamentals, last_close: float | None) -> float | None:
    pe = info_num(fund, "trailingPE")
    if pe is not None:
        return pe
    eps = info_num(fund, "trailingEps")
    if eps is not None and eps > 0 and last_close is not None:
        return last_close / eps
    return None


def _price_to_book(fund: Fundamentals, last_close: float | None) -> float | None:
    pb = info_num(fund, "priceToBook")
    if pb is not None:
        return pb
    bvps = info_num(fund, "bookValue")  # yfinance: book value per share
    if bvps is not None and bvps > 0 and last_close is not None:
        return last_close / bvps
    return None


def _eps_cagr(fund: Fundamentals, target_years: int = 3) -> float | None:
    """Annual EPS CAGR over up to ``target_years`` (needs >= 3 data points)."""
    eps = annual_values(fund.income, "Diluted EPS", "Basic EPS")
    if not eps or len(eps) < 3:
        return None
    k = min(target_years, len(eps) - 1)
    latest, base = eps[0], eps[k]
    if latest is None or base is None or base <= 0 or latest <= 0:
        return None
    return (latest / base) ** (1.0 / k) - 1.0


def _pe_history(fund: Fundamentals, close: pd.Series | None) -> list[float] | None:
    """Approximate historical P/E points: price at each fiscal year end
    divided by that fiscal year's EPS.  Limited by the ~3y price window
    and ~4y of yfinance annual statements, so this is a rough proxy for
    the "own 5y/10y P/E median" criteria (documented in batch/README.md).
    Needs >= 3 points, else None.
    """
    if close is None:
        return None
    eps_row = stmt_row(fund.income, "Diluted EPS", "Basic EPS")
    if eps_row is None:
        return None
    points = []
    for date, val in eps_row.items():
        eps = num(val)
        if eps is None or eps <= 0:
            continue
        try:
            px = close.asof(pd.Timestamp(date))
        except (TypeError, ValueError):
            continue
        px = num(px)
        if px is not None and px > 0:
            points.append(px / eps)
    return points if len(points) >= 3 else None


def _fcf_ttm(fund: Fundamentals) -> float | None:
    """Free cash flow TTM: sum of last 4 quarters, else info, else last FY."""
    row = stmt_row(fund.cashflow_q, "Free Cash Flow")
    if row is not None and len(row) >= 4:
        vals = [num(v) for v in row.iloc[:4]]
        if all(v is not None for v in vals):
            return sum(vals)  # type: ignore[arg-type]
    fcf = info_num(fund, "freeCashflow")
    if fcf is not None:
        return fcf
    vals_a = annual_values(fund.cashflow, "Free Cash Flow")
    return vals_a[0] if vals_a else None


def _ebit_ttm(fund: Fundamentals) -> float | None:
    """EBIT TTM: sum of last 4 quarterly EBIT/Operating Income, else annual."""
    row = stmt_row(fund.income_q, "EBIT", "Operating Income")
    if row is not None and len(row) >= 4:
        vals = [num(v) for v in row.iloc[:4]]
        if all(v is not None for v in vals):
            return sum(vals)  # type: ignore[arg-type]
    vals_a = annual_values(fund.income, "EBIT", "Operating Income")
    return vals_a[0] if vals_a else None


def _dividend_paid_recently(fund: Fundamentals) -> bool | None:
    """Any dividend within the last ~13 months; empty history = False."""
    if fund.dividends is None:
        rate = info_num(fund, "dividendRate")
        return None if rate is None else rate > 0
    if fund.dividends.empty:
        return False
    idx = fund.dividends.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=400)
    return bool((idx >= cutoff).any())


def _dividend_record(fund: Fundamentals, target_years: int = 10) -> bool | None:
    """Every one of the last up-to-``target_years`` full calendar years paid
    a dividend (window shrinks to the available dividend history).
    Empty dividend history = False; missing history = None.
    """
    if fund.dividends is None:
        return None
    if fund.dividends.empty:
        return False
    idx = fund.dividends.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    paid_years = {ts.year for ts in idx}
    this_year = pd.Timestamp.now().year
    first_year = max(min(paid_years), this_year - target_years)
    check_years = range(first_year, this_year)  # full calendar years only
    if not check_years:
        return bool(paid_years)
    return all(y in paid_years for y in check_years)


def _finish(model: str, sym: SymbolInfo, checks: dict[str, bool | None],
            metrics: dict, key_metric: str, extra: dict | None = None) -> dict:
    """Assemble the standard result dict from ordered criterion checks."""
    passed = [cid for cid, ok in checks.items() if ok is True]
    failed = [cid for cid, ok in checks.items() if ok is False]
    na = [cid for cid, ok in checks.items() if ok is None]
    total = len(checks)
    result = {
        "model": model,
        "symbol": sym.yf_ticker,
        "code": sym.code,
        "name": sym.name,
        "board": sym.board,
        "passed": passed,
        "failed": failed,
        "na": na,
        "score": len(passed),
        "total": total,
        "pct": round(100.0 * len(passed) / total, 1) if total else 0.0,
        "metrics": metrics,
        "key_metric": key_metric,
    }
    if extra:
        result.update(extra)
    return result


def _fmt(value: float | None, spec: str = ".2f", suffix: str = "") -> str:
    return "—" if value is None else format(value, spec) + suffix


# ---------------------------------------------------------------------------
# 3.1 葛拉漢 防禦型投資人 (G1–G7)
# ---------------------------------------------------------------------------

def score_graham(sym: SymbolInfo, prices: pd.DataFrame | None,
                 fund: Fundamentals, ctx: MarketContext) -> dict:
    close = close_series(prices)
    last = float(close.iloc[-1]) if close is not None else None
    checks: dict[str, bool | None] = {}

    # G1 market cap: US >= $2B, TW >= NT$30B
    mcap = info_num(fund, "marketCap")
    threshold = 30e9 if ctx.market == "tw" else 2e9
    checks["G1"] = None if mcap is None else mcap >= threshold

    # G2 current ratio >= 2
    cr = _current_ratio(fund)
    checks["G2"] = None if cr is None else cr >= 2.0

    # G3 long-term debt <= net working capital
    ca = _bal_latest(fund, "Current Assets")
    cl = _bal_latest(fund, "Current Liabilities")
    if ca is None or cl is None:
        checks["G3"] = None
    else:
        ltd = _bal_latest(fund, "Long Term Debt",
                          "Long Term Debt And Capital Lease Obligation")
        # A missing LT-debt row on an otherwise present balance sheet means
        # the company carries none (yfinance omits zero rows).
        checks["G3"] = (ltd or 0.0) <= (ca - cl)

    # G4 EPS positive in every available fiscal year (target 10y, shrinks)
    eps = annual_values(fund.income, "Diluted EPS", "Basic EPS")
    checks["G4"] = None if not eps or len(eps) < 2 else all(e > 0 for e in eps)

    # G5 dividend paid in every available year
    checks["G5"] = _dividend_record(fund)

    # G6 EPS growth: latest FY >= earliest available FY * 1.33
    if not eps or len(eps) < 2:
        checks["G6"] = None
    elif eps[-1] <= 0:
        checks["G6"] = False  # no meaningful base year
    else:
        checks["G6"] = eps[0] >= eps[-1] * 1.33

    # G7 P/E * P/B <= 22.5
    pe = _trailing_pe(fund, last)
    pb = _price_to_book(fund, last)
    pe_pb = pe * pb if pe is not None and pb is not None else None
    checks["G7"] = None if pe_pb is None else pe_pb <= 22.5

    metrics = {"pe_pb": pe_pb, "pe": pe, "pb": pb, "market_cap": mcap}
    return _finish("graham", sym, checks, metrics,
                   f"P/E×P/B={_fmt(pe_pb, '.1f')}")


# ---------------------------------------------------------------------------
# 3.2 巴菲特 質量護城河 (B1–B7)
# ---------------------------------------------------------------------------

def score_buffett(sym: SymbolInfo, prices: pd.DataFrame | None,
                  fund: Fundamentals, ctx: MarketContext) -> dict:
    checks: dict[str, bool | None] = {}

    roe = info_num(fund, "returnOnEquity")
    if roe is None:
        equity = annual_values(fund.balance, "Stockholders Equity")
        ni = annual_values(fund.income, "Net Income")
        if ni and equity and equity[0] > 0:
            roe = ni[0] / equity[0]
    checks["B1"] = None if roe is None else roe >= 0.15

    gm = info_num(fund, "grossMargins")
    checks["B2"] = None if gm is None else gm >= 0.40

    nm = info_num(fund, "profitMargins")
    checks["B3"] = None if nm is None else nm >= 0.10

    de = _debt_to_equity(fund)
    checks["B4"] = None if de is None else de <= 0.5

    fcf = annual_values(fund.cashflow, "Free Cash Flow")
    checks["B5"] = None if not fcf or len(fcf) < 3 else all(v > 0 for v in fcf[:3])

    # B6 operating margin not deteriorating: latest FY >= ~3y ago * 0.9
    pairs = _aligned_rows(fund.income, ("Operating Income",), ("Total Revenue",))
    if not pairs or len(pairs) < 3:
        checks["B6"] = None
    else:
        k = min(3, len(pairs) - 1)
        (oi_now, rev_now), (oi_then, rev_then) = pairs[0], pairs[k]
        if rev_now <= 0 or rev_then <= 0:
            checks["B6"] = None
        else:
            m_now, m_then = oi_now / rev_now, oi_then / rev_then
            checks["B6"] = m_now >= m_then * 0.9 if m_then > 0 else m_now >= m_then

    roa = info_num(fund, "returnOnAssets")
    checks["B7"] = None if roa is None else roa >= 0.07

    metrics = {"roe": roe, "gross_margin": gm, "net_margin": nm, "de": de}
    key = f"ROE={_fmt(roe * 100 if roe is not None else None, '.1f', '%')}"
    return _finish("buffett", sym, checks, metrics, key)


# ---------------------------------------------------------------------------
# 3.3 林區 PEG 成長分類 (L1–L5 + growth-class label, label not scored)
# ---------------------------------------------------------------------------

def score_lynch(sym: SymbolInfo, prices: pd.DataFrame | None,
                fund: Fundamentals, ctx: MarketContext) -> dict:
    close = close_series(prices)
    last = float(close.iloc[-1]) if close is not None else None
    checks: dict[str, bool | None] = {}

    cagr = _eps_cagr(fund, target_years=3)
    growth_pct = cagr * 100.0 if cagr is not None else None
    if growth_pct is None:
        label = "無法分類"
    elif growth_pct < 10:
        label = "緩慢成長"
    elif growth_pct <= 20:
        label = "穩健成長"
    else:
        label = "快速成長"

    pe = _trailing_pe(fund, last)
    peg = pe / growth_pct if pe is not None and growth_pct and growth_pct > 0 else None
    checks["L1"] = None if peg is None else peg <= 1.0

    checks["L2"] = None if growth_pct is None else 10.0 <= growth_pct <= 50.0

    de = _debt_to_equity(fund)
    checks["L3"] = None if de is None else de <= 0.6

    pe_hist = _pe_history(fund, close)
    if pe is None or pe_hist is None:
        checks["L4"] = None
    else:
        checks["L4"] = pe < statistics.median(pe_hist)

    rev = annual_values(fund.income, "Total Revenue")
    checks["L5"] = None if not rev or len(rev) < 2 else rev[0] > rev[1]

    metrics = {"peg": peg, "eps_cagr_pct": growth_pct, "pe": pe}
    key = f"PEG={_fmt(peg)}｜{label}"
    return _finish("lynch", sym, checks, metrics, key, extra={"label": label})


# ---------------------------------------------------------------------------
# 3.4 葛林布萊特 神奇公式 (M1–M2 thresholds; true double ranking in rank.py)
# ---------------------------------------------------------------------------

def score_greenblatt(sym: SymbolInfo, prices: pd.DataFrame | None,
                     fund: Fundamentals, ctx: MarketContext) -> dict:
    checks: dict[str, bool | None] = {}

    ebit = _ebit_ttm(fund)
    ev = info_num(fund, "enterpriseValue")
    earnings_yield = ebit / ev if ebit is not None and ev is not None and ev > 0 else None
    checks["M1"] = None if earnings_yield is None else earnings_yield >= 0.08

    # ROIC ~= EBIT / (net working capital + net fixed assets)  (Greenblatt)
    ca = _bal_latest(fund, "Current Assets")
    cl = _bal_latest(fund, "Current Liabilities")
    ppe = _bal_latest(fund, "Net PPE")
    roic = None
    if ebit is not None and ca is not None and cl is not None and ppe is not None:
        invested = (ca - cl) + ppe
        if invested > 0:
            roic = ebit / invested
    checks["M2"] = None if roic is None else roic >= 0.20

    metrics = {"earnings_yield": earnings_yield, "roic": roic}
    key = (f"EY={_fmt(earnings_yield * 100 if earnings_yield is not None else None, '.1f', '%')}"
           f"｜ROIC={_fmt(roic * 100 if roic is not None else None, '.1f', '%')}")
    return _finish("greenblatt", sym, checks, metrics, key,
                   extra={"earnings_yield": earnings_yield, "roic": roic})


# ---------------------------------------------------------------------------
# 3.5 歐尼爾 CANSLIM (C1–C7)
# ---------------------------------------------------------------------------

def _quarterly_eps_yoy(fund: Fundamentals) -> float | None:
    """Latest quarterly EPS YoY growth (fraction); None if unavailable.

    A negative-to-positive turnaround counts as a large positive number.
    """
    row = stmt_row(fund.income_q, "Diluted EPS", "Basic EPS")
    if row is not None and len(row) >= 2:
        latest_date = row.index[0]
        latest = num(row.iloc[0])
        target = pd.Timestamp(latest_date) - pd.Timedelta(days=365)
        best, best_gap = None, pd.Timedelta(days=60)
        for date, val in row.items():
            gap = abs(pd.Timestamp(date) - target)
            if gap <= best_gap:
                best, best_gap = num(val), gap
        if latest is not None and best is not None:
            if best > 0:
                return latest / best - 1.0
            return 10.0 if latest > 0 else None  # turnaround counts as pass
    return info_num(fund, "earningsQuarterlyGrowth")


def score_canslim(sym: SymbolInfo, prices: pd.DataFrame | None,
                  fund: Fundamentals, ctx: MarketContext) -> dict:
    close = close_series(prices)
    checks: dict[str, bool | None] = {}

    # C1 (C): latest quarterly EPS YoY >= 25%
    yoy = _quarterly_eps_yoy(fund)
    checks["C1"] = None if yoy is None else yoy >= 0.25

    # C2 (A): annual EPS 3y CAGR >= 25% or ROE >= 17%
    cagr = _eps_cagr(fund, target_years=3)
    roe = info_num(fund, "returnOnEquity")
    if cagr is None and roe is None:
        checks["C2"] = None
    else:
        checks["C2"] = (cagr is not None and cagr >= 0.25) or \
                       (roe is not None and roe >= 0.17)

    # C3 (N): within 15% of the 52-week high
    win = _window(close, TRADING_DAYS_1Y) if close is not None else None
    if win is None:
        checks["C3"] = None
    else:
        checks["C3"] = float(win.iloc[-1]) >= float(win.max()) * 0.85

    # C4 (S): 10-day average volume > 50-day average volume
    vol = prices["Volume"].dropna() if prices is not None and "Volume" in prices.columns else None
    if vol is None or len(vol) < 50:
        checks["C4"] = None
    else:
        checks["C4"] = float(vol.iloc[-10:].mean()) > float(vol.iloc[-50:].mean())

    # C5 (L): true RS Rating percentile across the scanned universe >= 70
    rs = ctx.rs_percentile.get(sym.yf_ticker)
    checks["C5"] = None if rs is None else rs >= 70.0

    # C6 (I): 50-day up-day volume >= down-day volume
    if close is None or vol is None or len(close) < 51:
        checks["C6"] = None
    else:
        chg = close.diff().iloc[-50:]
        v50 = vol.reindex(chg.index).fillna(0.0)
        up_vol = float(v50[chg > 0].sum())
        down_vol = float(v50[chg < 0].sum())
        checks["C6"] = up_vol >= down_vol

    # C7 (M): benchmark index above its own 50MA and 200MA
    idx = ctx.index_close
    if idx is None or len(idx) < 200:
        checks["C7"] = None
    else:
        last_idx = float(idx.iloc[-1])
        checks["C7"] = last_idx > _sma(idx, 50) and last_idx > _sma(idx, 200)

    metrics = {"rs": rs, "eps_yoy_q": yoy}
    return _finish("canslim", sym, checks, metrics, f"RS={_fmt(rs, '.0f')}")


# ---------------------------------------------------------------------------
# 3.6 米奈爾維尼 趨勢樣板 (T1–T8)
# ---------------------------------------------------------------------------

def score_minervini(sym: SymbolInfo, prices: pd.DataFrame | None,
                    fund: Fundamentals, ctx: MarketContext) -> dict:
    close = close_series(prices)
    checks: dict[str, bool | None] = {}
    last = float(close.iloc[-1]) if close is not None and not close.empty else None

    ma50 = _sma(close, 50) if close is not None else None
    ma150 = _sma(close, 150) if close is not None else None
    ma200 = _sma(close, 200) if close is not None else None
    ma200_prev = _sma_ago(close, 200, 21) if close is not None else None

    checks["T1"] = None if last is None or ma150 is None or ma200 is None else \
        last > ma150 and last > ma200
    checks["T2"] = None if ma150 is None or ma200 is None else ma150 > ma200
    checks["T3"] = None if ma200 is None or ma200_prev is None else ma200 > ma200_prev
    checks["T4"] = None if ma50 is None or ma150 is None or ma200 is None else \
        ma50 > ma150 and ma50 > ma200
    checks["T5"] = None if last is None or ma50 is None else last > ma50

    win = _window(close, TRADING_DAYS_1Y) if close is not None else None
    if win is None or last is None:
        checks["T6"] = None
        checks["T7"] = None
    else:
        lo, hi = float(win.min()), float(win.max())
        checks["T6"] = last >= lo * 1.30
        checks["T7"] = last >= hi * 0.75

    # T8: RS line (price/index) above its level 13 weeks ago, and true
    # RS Rating percentile >= 70 (the batch computes real percentiles).
    rs = ctx.rs_percentile.get(sym.yf_ticker)
    idx = ctx.index_close
    if close is None or idx is None or rs is None:
        checks["T8"] = None
    else:
        common = close.index.intersection(idx.index)
        rs_line = (close.reindex(common) / idx.reindex(common)).dropna()
        if len(rs_line) <= TRADING_DAYS_13W:
            checks["T8"] = None
        else:
            rising = float(rs_line.iloc[-1]) > float(rs_line.iloc[-1 - TRADING_DAYS_13W])
            checks["T8"] = rising and rs >= 70.0

    ret6m = _return(close, TRADING_DAYS_6M) if close is not None else None
    metrics = {"rs": rs, "ret_6m": ret6m}
    return _finish("minervini", sym, checks, metrics, f"RS={_fmt(rs, '.0f')}")


# ---------------------------------------------------------------------------
# 3.7 施洛斯 深度價值 (S1–S6)
# ---------------------------------------------------------------------------

def score_schloss(sym: SymbolInfo, prices: pd.DataFrame | None,
                  fund: Fundamentals, ctx: MarketContext) -> dict:
    close = close_series(prices)
    last = float(close.iloc[-1]) if close is not None else None
    checks: dict[str, bool | None] = {}

    pb = _price_to_book(fund, last)
    checks["S1"] = None if pb is None else pb <= 1.0

    de = _debt_to_equity(fund)
    checks["S2"] = None if de is None else de <= 0.3

    # S3 price within 30% of the 3-year low
    if close is None or last is None or len(close) < TRADING_DAYS_1Y:
        checks["S3"] = None
    else:
        low3y = float(close.min())
        checks["S3"] = last <= low3y * 1.30 if low3y > 0 else None

    # S4 equity not destroyed: latest FY >= ~3 years ago
    equity = annual_values(fund.balance, "Stockholders Equity",
                           "Total Equity Gross Minority Interest")
    if not equity or len(equity) < 3:
        checks["S4"] = None
    else:
        k = min(3, len(equity) - 1)
        checks["S4"] = equity[0] >= equity[k]

    checks["S5"] = _dividend_paid_recently(fund)

    cr = _current_ratio(fund)
    checks["S6"] = None if cr is None else cr >= 1.5

    metrics = {"pb": pb, "de": de}
    return _finish("schloss", sym, checks, metrics, f"P/B={_fmt(pb)}")


# ---------------------------------------------------------------------------
# 3.8 坦伯頓 極端悲觀點 (P1–P6)
# ---------------------------------------------------------------------------

def score_templeton(sym: SymbolInfo, prices: pd.DataFrame | None,
                    fund: Fundamentals, ctx: MarketContext) -> dict:
    close = close_series(prices)
    last = float(close.iloc[-1]) if close is not None else None
    checks: dict[str, bool | None] = {}

    # P1 / P2 use the longest available downloaded history (~3y; DESIGN
    # targets 10y "or longest available" — see batch/README.md caveats).
    drawdown = None
    if close is None or last is None or len(close) < TRADING_DAYS_1Y:
        checks["P1"] = None
        checks["P2"] = None
    else:
        hi, lo = float(close.max()), float(close.min())
        drawdown = last / hi - 1.0 if hi > 0 else None
        checks["P1"] = None if drawdown is None else drawdown <= -0.40
        checks["P2"] = None if hi <= lo else (last - lo) / (hi - lo) <= 0.20

    # P3: P/E <= own historical median * 0.7
    pe = _trailing_pe(fund, last)
    pe_hist = _pe_history(fund, close)
    if pe is None or pe_hist is None:
        checks["P3"] = None
    else:
        checks["P3"] = pe <= statistics.median(pe_hist) * 0.7

    # P4: still alive — trailing EPS > 0 or FCF TTM > 0
    eps_ttm = info_num(fund, "trailingEps")
    fcf = _fcf_ttm(fund)
    if eps_ttm is None and fcf is None:
        checks["P4"] = None
    else:
        checks["P4"] = (eps_ttm is not None and eps_ttm > 0) or \
                       (fcf is not None and fcf > 0)

    # P5: book value per share >= ~5y ago * 0.8 (limited by ~4y statements)
    shares = annual_values(fund.balance, "Ordinary Shares Number",
                           "Share Issued")
    equity = annual_values(fund.balance, "Stockholders Equity",
                           "Total Equity Gross Minority Interest")
    if not equity or len(equity) < 3:
        checks["P5"] = None
    else:
        k = min(4, len(equity) - 1)
        if shares and len(shares) > k and shares[0] > 0 and shares[k] > 0:
            bvps_now = equity[0] / shares[0]
            bvps_then = equity[k] / shares[k]
        else:  # fall back to raw equity comparison
            bvps_now, bvps_then = equity[0], equity[k]
        checks["P5"] = bvps_now >= bvps_then * 0.8 if bvps_then > 0 else bvps_now >= bvps_then

    # P6: stopped falling — close >= 52-week low * 1.05
    win = _window(close, TRADING_DAYS_1Y) if close is not None else None
    if win is None or last is None:
        checks["P6"] = None
    else:
        checks["P6"] = last >= float(win.min()) * 1.05

    metrics = {"drawdown": drawdown, "pe": pe}
    key = f"自高點{_fmt(drawdown * 100 if drawdown is not None else None, '+.1f', '%')}"
    return _finish("templeton", sym, checks, metrics, key)


SCORERS = {
    "graham": score_graham,
    "buffett": score_buffett,
    "lynch": score_lynch,
    "greenblatt": score_greenblatt,
    "canslim": score_canslim,
    "minervini": score_minervini,
    "schloss": score_schloss,
    "templeton": score_templeton,
}


def score_all(sym: SymbolInfo, prices: pd.DataFrame | None,
              fund: Fundamentals, ctx: MarketContext) -> list[dict]:
    """Run all eight scorers for one symbol."""
    return [scorer(sym, prices, fund, ctx) for scorer in SCORERS.values()]
