"""Daily markdown reports + TradingView-importable watchlists.

Outputs under ``reports/<YYYY-MM-DD>/``:

* ``report_tw.md`` / ``report_us.md`` — 市場溫度計 + 八模型排行前五 + 通過統計
* ``watchlists/<model>_{tw,us}.txt`` — one ``EXCHANGE:SYMBOL`` per line,
  directly importable into a TradingView watchlist.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import MODEL_NAMES_ZH, MODELS

log = logging.getLogger(__name__)

MARKET_NAMES_ZH = {"tw": "台股", "us": "美股"}

DISCLAIMER = "所有數字都是每日批次更新的研究參考，不是投資建議"

# yfinance ``info["exchange"]`` -> TradingView exchange prefix (US).
_US_EXCHANGE_MAP = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "NYQ": "NYSE", "NYSE": "NYSE", "NYE": "NYSE",
    "ASE": "AMEX", "AMEX": "AMEX", "NYSEAMERICAN": "AMEX",
    "PCX": "AMEX", "ARCA": "AMEX",
    "BATS": "AMEX", "CBOE": "AMEX",
}


def tv_symbol(result: dict, exchange_hint: str | None) -> str:
    """TradingView symbol for a scored result.

    TW listed -> ``TWSE:2330``, TW OTC -> ``TPEX:5483``.  US symbols use
    the yfinance exchange code when recognised, defaulting to NASDAQ.
    """
    board = result.get("board")
    code = result.get("code", result.get("symbol", ""))
    if board == "twse":
        return f"TWSE:{code}"
    if board == "tpex":
        return f"TPEX:{code}"
    prefix = _US_EXCHANGE_MAP.get((exchange_hint or "").upper(), "NASDAQ")
    return f"{prefix}:{code}"


def _fmt_score(value: float | None, digits: int = 0) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _thermometer_section(thermo: dict, index_name: str) -> list[str]:
    lines = ["## 市場溫度計", ""]
    score = thermo.get("score")
    lines.append(f"- **馬克斯鐘擺**：{_fmt_score(score)} / 100（0 = 極度悲觀，100 = 極度樂觀）")
    lines.append(f"- **科斯托蘭尼雞蛋階段**：{thermo.get('phase', '—')}")
    lines.append(f"- 基準指數：{index_name}")
    lines.append("")
    lines.append("| 分項 | 原始值 | 0–100 分 |")
    lines.append("|---|---:|---:|")
    for comp in thermo.get("components", []):
        raw = "—" if comp.value is None else f"{comp.value:.2f}"
        lines.append(f"| {comp.name} | {raw} | {_fmt_score(comp.score)} |")
    lines.append("")
    lines.append("> 這是情境參考，不是買賣訊號。")
    lines.append("")
    return lines


def _model_section(model: str, top: list[dict], all_results: list[dict]) -> list[str]:
    name = MODEL_NAMES_ZH[model]
    lines = [f"### {name}", ""]
    if not top:
        lines += ["（本次掃描無可排名的標的 —— 資料缺漏）", ""]
        return lines
    total = top[0]["total"]
    lines.append(f"共 {total} 條準則；排行前五（依通過 % 排序，同分依模型關鍵指標）：")
    if model == "greenblatt":
        lines[-1] = "神奇公式真雙排名（盈餘殖利率名次 + ROIC 名次，總和越小越好）前五："
    lines.append("")
    lines.append("| 排名 | 代號 | 名稱 | 得分 | % | 關鍵指標 | 未過條目 | 資料缺漏 |")
    lines.append("|---:|---|---|---|---:|---|---|---|")
    for i, r in enumerate(top, 1):
        failed = "、".join(r["failed"]) if r["failed"] else "—"
        na = "、".join(r["na"]) if r["na"] else "—"
        lines.append(
            f"| {i} | {r['code']} | {r['name']} | {r['score']}/{r['total']} "
            f"| {r['pct']:.0f}% | {r['key_metric']} | {failed} | {na} |"
        )
    # pass statistics for this model
    scored = len(all_results)
    if scored:
        full = sum(1 for r in all_results if r["score"] == r["total"])
        avg = sum(r["pct"] for r in all_results) / scored
        lines.append("")
        lines.append(f"通過統計：掃描 {scored} 檔，平均通過率 {avg:.0f}%，滿分 {full} 檔。")
    lines.append("")
    return lines


def _etf_section(etf_rows: list[dict]) -> list[str]:
    lines = ["## ETF 專區（向柏格致敬）", ""]
    lines.append("主動選股的對手組合永遠是「買下整個草堆」。以下為趨勢快照，不打分、不排名：")
    lines.append("")
    lines.append("| 代號 | 名稱 | 收盤 | 6月 % | 1年 % | 距200MA % | 52週位置 % |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")

    def fmt(v: float | None, digits: int = 1) -> str:
        return "—" if v is None else f"{v:.{digits}f}"

    for r in etf_rows:
        lines.append(
            f"| {r['tv']} | {r['name']} | {fmt(r.get('close'), 2)} "
            f"| {fmt(r.get('ret_6m'))} | {fmt(r.get('ret_1y'))} "
            f"| {fmt(r.get('dist_200ma'))} | {fmt(r.get('range_pos'), 0)} |"
        )
    lines.append("")
    return lines


def write_report(market: str, date_key: str, thermo: dict, index_name: str,
                 results_by_model: dict[str, list[dict]],
                 tops: dict[str, list[dict]], stats: dict,
                 exchange_hints: dict[str, str | None],
                 reports_root: Path,
                 etf_rows: list[dict] | None = None) -> Path:
    """Write ``report_<market>.md`` and the per-model watchlists.

    ``exchange_hints`` maps yfinance ticker -> ``info["exchange"]``.
    Returns the report path.
    """
    out_dir = reports_root / date_key
    out_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append(f"# 選股羅盤 每日報告 — {MARKET_NAMES_ZH.get(market, market)}（{date_key}）")
    lines.append("")
    lines += _thermometer_section(thermo, index_name)
    lines.append("## 八模型排行前五")
    lines.append("")
    for model in MODELS:
        lines += _model_section(model, tops.get(model, []),
                                results_by_model.get(model, []))
    if etf_rows:
        lines += _etf_section(etf_rows)
    lines.append("## 掃描統計")
    lines.append("")
    lines.append(f"- 掃描範圍：{stats.get('universe', 0)} 檔")
    lines.append(f"- 有價格資料並完成評分：{stats.get('scored', 0)} 檔")
    lines.append(f"- 略過（無價格資料或評分失敗）：{stats.get('skipped', 0)} 檔")
    if stats.get("note"):
        lines.append(f"- 備註：{stats['note']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"> {DISCLAIMER}")
    lines.append("")

    report_path = out_dir / f"report_{market}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("report written: %s", report_path)

    _write_watchlists(market, tops, exchange_hints, out_dir)
    if etf_rows:
        write_etf_watchlist(market, etf_rows, out_dir)
    return report_path


def _write_watchlists(market: str, tops: dict[str, list[dict]],
                      exchange_hints: dict[str, str | None], out_dir: Path) -> None:
    wl_dir = out_dir / "watchlists"
    wl_dir.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        top = tops.get(model, [])
        path = wl_dir / f"{model}_{market}.txt"
        symbols = [tv_symbol(r, exchange_hints.get(r["symbol"])) for r in top]
        path.write_text("\n".join(symbols) + ("\n" if symbols else ""), encoding="utf-8")
    log.info("watchlists written under %s", wl_dir)


def write_etf_watchlist(market: str, etf_rows: list[dict], out_dir: Path) -> None:
    wl_dir = out_dir / "watchlists"
    wl_dir.mkdir(parents=True, exist_ok=True)
    path = wl_dir / f"etf_{market}.txt"
    symbols = [r["tv"] for r in etf_rows]
    path.write_text("\n".join(symbols) + ("\n" if symbols else ""), encoding="utf-8")
