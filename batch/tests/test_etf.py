"""Offline tests for the ETF 專區 (Bogle corner).

Runnable either way::

    cd batch && python -m pytest tests/test_etf.py -q
    cd batch && python tests/test_etf.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compass.etf import etf_snapshot, load_etf_list  # noqa: E402
from compass.report import _etf_section  # noqa: E402


def _price_frame(closes: np.ndarray) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-07-08", periods=len(closes))
    return pd.DataFrame({"Close": closes, "High": closes * 1.01,
                         "Low": closes * 0.99, "Volume": 1_000_000}, index=idx)


def test_load_etf_lists() -> None:
    for market in ("tw", "us"):
        etfs = load_etf_list(market)
        assert len(etfs) >= 5, f"{market} ETF list too short"
        for e in etfs:
            assert ":" in e.tv_symbol, f"bad TV symbol {e.tv_symbol}"
            assert e.yf_ticker and e.name


def test_snapshot_uptrend() -> None:
    closes = np.linspace(80, 120, 400)  # steady uptrend, ~2y of history
    snap = etf_snapshot(_price_frame(closes))
    assert snap is not None
    assert abs(snap["close"] - 120) < 1e-6
    assert snap["ret_6m"] is not None and snap["ret_6m"] > 0
    assert snap["ret_1y"] is not None and snap["ret_1y"] > 0
    assert snap["dist_200ma"] is not None and snap["dist_200ma"] > 0
    assert snap["range_pos"] is not None and snap["range_pos"] > 99


def test_snapshot_short_history() -> None:
    closes = np.linspace(100, 110, 30)  # under 60 bars -> no snapshot
    assert etf_snapshot(_price_frame(closes)) is None
    closes = np.linspace(100, 110, 100)  # 60-200 bars: no 200MA yet
    snap = etf_snapshot(_price_frame(closes))
    assert snap is not None
    assert snap["dist_200ma"] is None
    assert snap["ret_1y"] is None


def test_etf_report_section() -> None:
    rows = [
        {"tv": "AMEX:VTI", "name": "Vanguard Total", "close": 300.0,
         "ret_6m": 5.1, "ret_1y": 12.3, "dist_200ma": 4.5, "range_pos": 88.0},
        {"tv": "TWSE:0050", "name": "元大台灣50"},  # no price data -> em dashes
    ]
    text = "\n".join(_etf_section(rows))
    assert "ETF 專區" in text
    assert "AMEX:VTI" in text and "TWSE:0050" in text
    assert "| — | — | — | — |" in text  # missing snapshot renders as gaps


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
