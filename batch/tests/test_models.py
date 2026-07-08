"""Offline fixture tests for the compass scoring pipeline.

Runs with no network at all — synthetic price paths + hand-built
statement DataFrames shaped like yfinance output.  Runnable either way::

    cd batch && python -m pytest tests/ -q
    cd batch && python tests/test_models.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compass import MODELS  # noqa: E402
from compass.data import Fundamentals  # noqa: E402
from compass.models import (  # noqa: E402
    MarketContext,
    close_series,
    score_all,
    score_buffett,
    score_canslim,
    score_graham,
    score_greenblatt,
    score_lynch,
    score_minervini,
    score_schloss,
    score_templeton,
)
from compass.rank import (  # noqa: E402
    breadth_above_200ma,
    magic_formula_rank,
    rs_percentiles,
    six_month_returns,
    top5,
)
from compass.report import tv_symbol, write_report  # noqa: E402
from compass.thermometer import compute_thermometer  # noqa: E402
from compass.universe import SymbolInfo  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def make_prices(start: float, end: float, days: int = 800,
                vol_start: float = 1e6, vol_end: float = 2e6) -> pd.DataFrame:
    """Smooth geometric price path ending today, business-day index."""
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    close = np.geomspace(start, end, days)
    volume = np.linspace(vol_start, vol_end, days)
    return pd.DataFrame(
        {"Close": close, "High": close * 1.01, "Low": close * 0.99,
         "Volume": volume},
        index=index,
    )


def make_downtrend_prices(days: int = 800) -> pd.DataFrame:
    """100 -> 60 -> 38 decline (steeper recently, keeping the price below
    its 200MA), then a small bounce to 41 (止跌) at the very end."""
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    leg1 = np.geomspace(100, 60, 540)
    leg2 = np.geomspace(60, 38, days - 540 - 30)
    bounce = np.geomspace(38, 41, 30)
    close = np.concatenate([leg1, leg2, bounce])
    return pd.DataFrame(
        {"Close": close, "High": close * 1.01, "Low": close * 0.99,
         "Volume": np.full(days, 5e5)},
        index=index,
    )


def make_accel_index(days: int = 1500) -> pd.DataFrame:
    """Index that accelerates recently, with deterministic wiggle so the
    percentile components have a real distribution to rank against."""
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    slow = np.geomspace(100, 110, days - 260)
    fast = np.geomspace(110, 140, 260)
    close = np.concatenate([slow, fast]) * (1 + 0.01 * np.sin(np.arange(days)))
    return pd.DataFrame(
        {"Close": close, "High": close * 1.01, "Low": close * 0.99,
         "Volume": np.full(days, 1e9)},
        index=index,
    )


ANNUAL_COLS = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
Q_COLS = pd.to_datetime(["2026-03-31", "2025-12-31", "2025-09-30",
                         "2025-06-30", "2025-03-31"])


def _stmt(rows: dict[str, list[float]], cols: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({c: {k: v[i] for k, v in rows.items()}
                         for i, c in enumerate(cols)})


def make_goodco_fund() -> Fundamentals:
    """A quality growth company: passes Buffett 7/7, CANSLIM, Minervini."""
    dividends = pd.Series(
        [1.0] * 11,
        index=pd.to_datetime([f"{y}-06-15" for y in range(2016, 2027)]),
    )
    return Fundamentals(
        info={
            "marketCap": 50e9, "currentRatio": 2.5, "trailingPE": 20.0,
            "priceToBook": 3.0, "returnOnEquity": 0.25, "grossMargins": 0.55,
            "profitMargins": 0.20, "debtToEquity": 40.0, "returnOnAssets": 0.12,
            "trailingEps": 7.5, "enterpriseValue": 45e9, "dividendRate": 1.0,
            "exchange": "NMS", "earningsQuarterlyGrowth": 0.30,
        },
        income=_stmt({
            "Diluted EPS": [8.0, 6.0, 5.0, 4.0],
            "Total Revenue": [100e9, 90e9, 80e9, 70e9],
            "Operating Income": [30e9, 27e9, 24e9, 20e9],
            "Net Income": [20e9, 17e9, 15e9, 12e9],
        }, ANNUAL_COLS),
        income_q=_stmt({
            "Diluted EPS": [2.5, 2.1, 2.0, 1.9, 1.8],
            "EBIT": [8e9, 7.5e9, 7.5e9, 7e9, 6.5e9],
        }, Q_COLS),
        balance=_stmt({
            "Stockholders Equity": [40e9, 36e9, 32e9, 28e9],
            "Ordinary Shares Number": [1e9, 1e9, 1e9, 1e9],
        }, ANNUAL_COLS),
        balance_q=_stmt({
            "Current Assets": [30e9, 29e9],
            "Current Liabilities": [12e9, 12e9],
            "Long Term Debt": [10e9, 10e9],
            "Net PPE": [20e9, 19e9],
        }, Q_COLS[:2]),
        cashflow=_stmt({
            "Free Cash Flow": [10e9, 9e9, 8e9, 7e9],
        }, ANNUAL_COLS),
        cashflow_q=_stmt({
            "Free Cash Flow": [3e9, 2.5e9, 2.5e9, 2e9],
        }, Q_COLS[:4]),
        dividends=dividends,
    )


def make_cheapco_fund() -> Fundamentals:
    """A beaten-down deep-value company: passes Schloss 6/6."""
    dividends = pd.Series(
        [0.5] * 11,
        index=pd.to_datetime([f"{y}-06-15" for y in range(2016, 2027)]),
    )
    return Fundamentals(
        info={
            "marketCap": 1e9, "currentRatio": 1.8, "trailingPE": 6.0,
            "priceToBook": 0.7, "debtToEquity": 20.0, "trailingEps": 7.0,
            "exchange": "NYQ", "dividendRate": 0.5,
        },
        income=_stmt({
            "Diluted EPS": [7.0, 7.0, 7.0, 7.0],
            "Total Revenue": [10e9, 10e9, 10e9, 10e9],
            "Operating Income": [1e9, 1e9, 1e9, 1e9],
        }, ANNUAL_COLS),
        balance=_stmt({
            "Stockholders Equity": [10e9, 9.8e9, 9.9e9, 9.5e9],
            "Ordinary Shares Number": [1e8, 1e8, 1e8, 1e8],
        }, ANNUAL_COLS),
        dividends=dividends,
    )


def make_context() -> tuple[MarketContext, pd.DataFrame]:
    """Rising benchmark index (slower than GOODCO) + RS percentiles."""
    index_prices = make_prices(100, 130, days=1500)
    ctx = MarketContext(
        market="us",
        index_close=close_series(index_prices),
        rs_percentile={"GOODCO": 90.0, "CHEAPCO": 10.0},
    )
    return ctx, index_prices


GOOD_SYM = SymbolInfo(code="GOODCO", name="Good Co", market="us", board="us")
CHEAP_SYM = SymbolInfo(code="CHEAPCO", name="Cheap Co", market="us", board="us")
GOOD_PRICES = make_prices(50, 150)
CHEAP_PRICES = make_downtrend_prices()
CTX, INDEX_PRICES = make_context()


def _ids(result: dict, key: str) -> set[str]:
    return set(result[key])


# ---------------------------------------------------------------------------
# per-model tests
# ---------------------------------------------------------------------------

def test_graham() -> None:
    r = score_graham(GOOD_SYM, GOOD_PRICES, make_goodco_fund(), CTX)
    # everything passes except the G7 price defense (P/E 20 × P/B 3 = 60)
    assert _ids(r, "passed") == {"G1", "G2", "G3", "G4", "G5", "G6"}, r
    assert _ids(r, "failed") == {"G7"}, r
    assert r["na"] == [] and r["score"] == 6 and r["total"] == 7
    assert abs(r["pct"] - 85.7) < 0.1
    assert abs(r["metrics"]["pe_pb"] - 60.0) < 1e-9


def test_buffett() -> None:
    r = score_buffett(GOOD_SYM, GOOD_PRICES, make_goodco_fund(), CTX)
    assert r["score"] == 7 and r["failed"] == [] and r["na"] == [], r


def test_lynch() -> None:
    r = score_lynch(GOOD_SYM, GOOD_PRICES, make_goodco_fund(), CTX)
    # EPS 3y CAGR = (8/4)^(1/3)-1 = 26% -> 快速成長, PEG = 20/26 = 0.77
    assert r["label"] == "快速成長"
    assert {"L1", "L2", "L3", "L5"} <= _ids(r, "passed"), r
    assert "L4" not in r["na"], "3 P/E history points should be enough"
    assert abs(r["metrics"]["peg"] - 20.0 / 25.992) < 0.01


def test_greenblatt() -> None:
    r = score_greenblatt(GOOD_SYM, GOOD_PRICES, make_goodco_fund(), CTX)
    # EBIT TTM = 8+7.5+7.5+7 = 30e9; EY = 30/45 = 66.7%; ROIC = 30/38 = 78.9%
    assert _ids(r, "passed") == {"M1", "M2"}, r
    assert abs(r["earnings_yield"] - 30.0 / 45.0) < 1e-9
    assert abs(r["roic"] - 30.0 / 38.0) < 1e-9


def test_canslim() -> None:
    r = score_canslim(GOOD_SYM, GOOD_PRICES, make_goodco_fund(), CTX)
    # quarterly EPS YoY = 2.5/1.8-1 = 38.9%; RS 90; rising volume/index
    assert r["score"] == 7 and r["failed"] == [] and r["na"] == [], r


def test_minervini() -> None:
    r = score_minervini(GOOD_SYM, GOOD_PRICES, make_goodco_fund(), CTX)
    assert r["score"] == 8 and r["failed"] == [] and r["na"] == [], r
    weak = score_minervini(CHEAP_SYM, CHEAP_PRICES, make_cheapco_fund(), CTX)
    assert {"T1", "T2", "T4"} <= _ids(weak, "failed"), weak


def test_schloss() -> None:
    r = score_schloss(CHEAP_SYM, CHEAP_PRICES, make_cheapco_fund(), CTX)
    assert r["score"] == 6 and r["failed"] == [] and r["na"] == [], r
    growth = score_schloss(GOOD_SYM, GOOD_PRICES, make_goodco_fund(), CTX)
    assert {"S1", "S2", "S3"} <= _ids(growth, "failed"), growth


def test_templeton() -> None:
    r = score_templeton(CHEAP_SYM, CHEAP_PRICES, make_cheapco_fund(), CTX)
    # 42 vs 100 high = -58% drawdown; bottom 20% of range; bounced +10.5%
    assert {"P1", "P2", "P4", "P6"} <= _ids(r, "passed"), r
    assert r["metrics"]["drawdown"] < -0.55
    hot = score_templeton(GOOD_SYM, GOOD_PRICES, make_goodco_fund(), CTX)
    assert {"P1", "P2"} <= _ids(hot, "failed"), hot


def test_missing_data_is_na_not_pass() -> None:
    """No prices + empty fundamentals + empty context => every criterion
    is 資料缺漏 (never silently passed)."""
    empty = Fundamentals()
    empty_ctx = MarketContext(market="us")
    for r in score_all(GOOD_SYM, None, empty, empty_ctx):
        assert r["score"] == 0, r
        assert r["passed"] == [] and r["failed"] == []
        assert len(r["na"]) == r["total"], r
        assert r["pct"] == 0.0


# ---------------------------------------------------------------------------
# cross-sectional tests
# ---------------------------------------------------------------------------

def test_rs_percentiles() -> None:
    pct = rs_percentiles({"A": 0.5, "B": 0.1, "C": -0.2})
    assert pct["A"] == 100.0 and pct["C"] < pct["B"] < pct["A"]
    prices = {"GOODCO": GOOD_PRICES, "CHEAPCO": CHEAP_PRICES}
    returns = six_month_returns(prices)
    assert returns["GOODCO"] > returns["CHEAPCO"]
    assert rs_percentiles(returns)["GOODCO"] == 100.0


def test_breadth() -> None:
    prices = {"GOODCO": GOOD_PRICES, "CHEAPCO": CHEAP_PRICES}
    breadth = breadth_above_200ma(prices)
    assert breadth == 50.0  # uptrend above its 200MA, downtrend below


def test_magic_formula_double_ranking() -> None:
    def fake(sym: str, ey: float, roic: float) -> dict:
        return {"model": "greenblatt", "symbol": sym, "code": sym, "name": sym,
                "board": "us", "passed": [], "failed": [], "na": [],
                "score": 0, "total": 2, "pct": 0.0, "metrics": {},
                "key_metric": "", "earnings_yield": ey, "roic": roic}

    a, b, c = fake("A", 0.10, 0.30), fake("B", 0.20, 0.10), fake("C", 0.05, 0.05)
    d = fake("D", None, 0.50)  # missing EY -> excluded
    ranked = magic_formula_rank([a, b, c, d])
    assert [r["symbol"] for r in ranked][:2] in (["A", "B"], ["B", "A"])
    assert ranked[-1]["symbol"] == "C"
    assert all("magic_rank" in r["metrics"] for r in ranked)
    assert all(r["symbol"] != "D" for r in ranked)
    # combined ranks: A = 2+1 = 3, B = 1+2 = 3, C = 3+3 = 6
    assert ranked[-1]["metrics"]["combined_rank"] == 6


def test_top5_tiebreak() -> None:
    def graham_like(sym: str, pct: float, pe_pb: float | None) -> dict:
        return {"model": "graham", "symbol": sym, "code": sym, "name": sym,
                "board": "us", "passed": [], "failed": [], "na": [],
                "score": 0, "total": 7, "pct": pct,
                "metrics": {"pe_pb": pe_pb}, "key_metric": ""}

    rows = [graham_like("HIGHPE", 71.4, 30.0), graham_like("LOWPE", 71.4, 5.0),
            graham_like("BEST", 85.7, 40.0), graham_like("NOPE", 71.4, None)]
    top = top5(rows, "graham")
    assert [r["symbol"] for r in top[:3]] == ["BEST", "LOWPE", "HIGHPE"]
    assert top[3]["symbol"] == "NOPE"  # missing metric sorts last in its group


# ---------------------------------------------------------------------------
# thermometer tests
# ---------------------------------------------------------------------------

def test_thermometer_uptrend() -> None:
    thermo = compute_thermometer(make_accel_index(), breadth_pct=80.0)
    assert thermo["score"] is not None and thermo["score"] >= 60.0, thermo
    assert thermo["phase"].startswith("上升"), thermo
    assert len(thermo["components"]) == 5
    assert all(c.score is not None for c in thermo["components"])


def test_thermometer_missing_index() -> None:
    thermo = compute_thermometer(None, breadth_pct=None)
    assert thermo["score"] is None
    assert "無法計算" in thermo["phase"]
    partial = compute_thermometer(None, breadth_pct=15.0)
    assert partial["score"] == 15.0  # only breadth available
    assert partial["phase"] == "下降 B3 誇張（過賣）"


# ---------------------------------------------------------------------------
# report / watchlist tests (offline end-to-end)
# ---------------------------------------------------------------------------

def test_tv_symbol_mapping() -> None:
    tw = {"board": "twse", "code": "2330", "symbol": "2330.TW"}
    otc = {"board": "tpex", "code": "5483", "symbol": "5483.TWO"}
    us = {"board": "us", "code": "AAPL", "symbol": "AAPL"}
    assert tv_symbol(tw, None) == "TWSE:2330"
    assert tv_symbol(otc, None) == "TPEX:5483"
    assert tv_symbol(us, "NMS") == "NASDAQ:AAPL"
    assert tv_symbol(us, "NYQ") == "NYSE:AAPL"
    assert tv_symbol(us, None) == "NASDAQ:AAPL"  # defensive default


def test_end_to_end_report() -> None:
    results_by_model: dict[str, list[dict]] = {m: [] for m in MODELS}
    for sym, prices, fund in [(GOOD_SYM, GOOD_PRICES, make_goodco_fund()),
                              (CHEAP_SYM, CHEAP_PRICES, make_cheapco_fund())]:
        for r in score_all(sym, prices, fund, CTX):
            results_by_model[r["model"]].append(r)
    tops = {m: top5(results_by_model[m], m) for m in MODELS}
    thermo = compute_thermometer(INDEX_PRICES, breadth_pct=50.0)

    with tempfile.TemporaryDirectory() as tmp:
        report_path = write_report(
            market="us", date_key="2026-07-08", thermo=thermo,
            index_name="S&P 500（^GSPC）",
            results_by_model=results_by_model, tops=tops,
            stats={"universe": 2, "scored": 2, "skipped": 0},
            exchange_hints={"GOODCO": "NMS", "CHEAPCO": "NYQ"},
            reports_root=Path(tmp),
        )
        text = report_path.read_text(encoding="utf-8")
        assert "市場溫度計" in text and "馬克斯鐘擺" in text
        assert "米奈爾維尼 趨勢樣板" in text and "GOODCO" in text
        assert "所有數字都是每日批次更新的研究參考，不是投資建議" in text
        wl = report_path.parent / "watchlists"
        minervini = (wl / "minervini_us.txt").read_text(encoding="utf-8")
        assert minervini.splitlines()[0] == "NASDAQ:GOODCO"
        schloss = (wl / "schloss_us.txt").read_text(encoding="utf-8")
        assert schloss.splitlines()[0] == "NYSE:CHEAPCO"
        assert (wl / "greenblatt_us.txt").exists()


# ---------------------------------------------------------------------------
# plain-python runner
# ---------------------------------------------------------------------------

def main() -> int:
    failures = 0
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
