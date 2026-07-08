"""Market thermometer (DESIGN.md section 4).

Marks pendulum (0 = extreme pessimism … 100 = extreme optimism) is the
mean of the available components, each normalised to 0-100 with a
5-year percentrank of the index's own history:

1. 趨勢熱度  distance of the index above/below its 200MA, percentile
2. 動能熱度  126-day return, percentile
3. 區間位置  position inside the 52-week range (0-100 directly)
4. 市場寬度  % of the scanned universe above its own 200MA (from rank.py)
5. 波動自滿度 inverted percentile of 21-day realised volatility
   (low volatility = complacency = optimism)

The Kostolany egg phase is classified from the pendulum score plus the
200MA trend direction per the table in DESIGN.md section 4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .models import close_series

FIVE_YEARS = 5 * 252
TRADING_DAYS_6M = 126


@dataclass
class Component:
    cid: str
    name: str           # Traditional Chinese, matching DESIGN.md
    value: float | None  # raw value for display
    score: float | None  # normalised 0-100 (percentile)


def percent_rank(history: pd.Series, value: float) -> float | None:
    """Share of ``history`` values <= ``value``, as 0-100."""
    hist = history.dropna()
    if hist.empty:
        return None
    return 100.0 * float((hist <= value).mean())


def _components(index_close: pd.Series | None,
                breadth_pct: float | None) -> list[Component]:
    comps: list[Component] = []

    dist_pr = mom_pr = range_pos = vol_pr = None
    dist_now = mom_now = vol_now = None
    if index_close is not None and len(index_close) >= 200:
        close = index_close
        ma200 = close.rolling(200).mean()
        dist = (close / ma200 - 1.0).dropna()
        if not dist.empty:
            dist_now = float(dist.iloc[-1])
            dist_pr = percent_rank(dist.iloc[-FIVE_YEARS:], dist_now)

        mom = close.pct_change(TRADING_DAYS_6M).dropna()
        if not mom.empty:
            mom_now = float(mom.iloc[-1])
            mom_pr = percent_rank(mom.iloc[-FIVE_YEARS:], mom_now)

        win = close.iloc[-252:]
        lo, hi = float(win.min()), float(win.max())
        if hi > lo:
            range_pos = 100.0 * (float(close.iloc[-1]) - lo) / (hi - lo)

        vol = (close.pct_change().rolling(21).std() * math.sqrt(252)).dropna()
        if not vol.empty:
            vol_now = float(vol.iloc[-1])
            pr = percent_rank(vol.iloc[-FIVE_YEARS:], vol_now)
            vol_pr = None if pr is None else 100.0 - pr  # low vol = complacent

    comps.append(Component("trend", "趨勢熱度（指數距 200MA 百分位）", dist_now, dist_pr))
    comps.append(Component("momentum", "動能熱度（126 日報酬百分位）", mom_now, mom_pr))
    comps.append(Component("range", "區間位置（52 週）", range_pos, range_pos))
    comps.append(Component("breadth", "市場寬度（站上 200MA 比例）", breadth_pct, breadth_pct))
    comps.append(Component("volatility", "波動自滿度（21 日波動率反轉）", vol_now, vol_pr))
    return comps


def _egg_phase(score: float, index_close: pd.Series | None) -> str:
    """Kostolany egg phase per the DESIGN.md section 4 table."""
    last = ma200 = ma200_prev = None
    if index_close is not None and len(index_close) >= 221:
        last = float(index_close.iloc[-1])
        ma200 = float(index_close.iloc[-200:].mean())
        ma200_prev = float(index_close.iloc[-221:-21].mean())

    if score >= 80:
        return "上升 A3 誇張（過熱）"
    if score >= 60:
        if last is None or ma200 is None:
            return "上升 A2 相隨"
        return "上升 A2 相隨" if last > ma200 else "下降 B1 修正（高檔轉弱）"
    if score >= 40:
        if ma200 is None or ma200_prev is None:
            return "下降 B2 相隨"
        return "上升 A1 修正（初升）" if ma200 > ma200_prev else "下降 B2 相隨"
    if score >= 20:
        return "下降 B2 相隨（加速）"
    return "下降 B3 誇張（過賣）"


def compute_thermometer(index_prices: pd.DataFrame | None,
                        breadth_pct: float | None) -> dict:
    """Pendulum score + egg phase + component breakdown.

    Returns ``{score, phase, components: [Component, ...]}``; score is
    None when no component could be computed (e.g. index download failed).
    """
    index_close = close_series(index_prices)
    comps = _components(index_close, breadth_pct)
    scores = [c.score for c in comps if c.score is not None]
    if not scores:
        return {"score": None, "phase": "無法計算（指數資料缺漏）", "components": comps}
    score = sum(scores) / len(scores)
    return {"score": score, "phase": _egg_phase(score, index_close), "components": comps}
