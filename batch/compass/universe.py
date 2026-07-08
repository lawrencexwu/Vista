"""Symbol universes for the daily batch.

TW: TWSE listed companies (上市) from the TWSE OpenAPI and TPEX OTC
companies (上櫃) from the TPEX OpenAPI.  If either endpoint is
unreachable the code falls back to a bundled static sample list
(``data/tw_sample.txt``) so the batch still produces a report.

US: bundled static membership lists for the S&P 500 and Nasdaq 100
(``data/sp500.txt`` / ``data/nasdaq100.txt``); refresh them manually
with ``scripts/refresh_universe.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"

TWSE_LISTED_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
# Primary: OTC company basic-info list (mirror of the TWSE endpoint).
TPEX_COMPANY_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
# Fallback: mainboard daily quotes (also carries code + name).
TPEX_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"

REQUEST_TIMEOUT = 30


@dataclass
class SymbolInfo:
    """One scannable symbol."""

    code: str            # raw code, e.g. "2330" or "AAPL"
    name: str            # display name (Chinese for TW)
    market: str          # "tw" | "us"
    board: str           # "twse" | "tpex" | "us"

    @property
    def yf_ticker(self) -> str:
        """yfinance ticker: TW listed -> {code}.TW, TW OTC -> {code}.TWO."""
        if self.market == "tw":
            suffix = ".TW" if self.board == "twse" else ".TWO"
            return self.code + suffix
        # yfinance uses "-" for share classes (BRK.B -> BRK-B).
        return self.code.replace(".", "-")


def _http_json(url: str) -> list[dict]:
    """GET a JSON array; raises on any failure."""
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"Accept": "application/json"})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"unexpected payload from {url}")
    return data


def _valid_tw_code(code: str) -> bool:
    """Keep common-stock style 4-digit codes; drop ETFs/warrants/TDRs."""
    return len(code) == 4 and code.isdigit()


def _fetch_twse_listed() -> list[SymbolInfo]:
    rows = _http_json(TWSE_LISTED_URL)
    out = []
    for row in rows:
        code = str(row.get("公司代號", "")).strip()
        name = str(row.get("公司簡稱") or row.get("公司名稱") or "").strip()
        if _valid_tw_code(code):
            out.append(SymbolInfo(code=code, name=name, market="tw", board="twse"))
    return out


def _fetch_tpex_otc() -> list[SymbolInfo]:
    try:
        rows = _http_json(TPEX_COMPANY_URL)
        key_code, key_name = "公司代號", "公司簡稱"
    except Exception as exc:  # noqa: BLE001 - fall back to the quotes endpoint
        log.warning("TPEX company list failed (%s); trying quotes endpoint", exc)
        rows = _http_json(TPEX_QUOTES_URL)
        key_code, key_name = "SecuritiesCompanyCode", "CompanyName"
    out = []
    for row in rows:
        code = str(row.get(key_code, "")).strip()
        name = str(row.get(key_name) or row.get("公司名稱") or row.get("CompanyName") or "").strip()
        if _valid_tw_code(code):
            out.append(SymbolInfo(code=code, name=name, market="tw", board="tpex"))
    return out


def _load_tw_sample() -> list[SymbolInfo]:
    out = []
    for line in (DATA_DIR / "tw_sample.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            out.append(SymbolInfo(code=parts[0], name=parts[1], market="tw", board=parts[2]))
    return out


def _load_us_list(filename: str) -> list[str]:
    tickers = []
    for line in (DATA_DIR / filename).read_text(encoding="utf-8").splitlines():
        line = line.strip().upper()
        if line and not line.startswith("#"):
            tickers.append(line)
    return tickers


def tw_universe() -> list[SymbolInfo]:
    """TWSE listed + TPEX OTC; static sample fallback on network failure."""
    symbols: list[SymbolInfo] = []
    try:
        symbols += _fetch_twse_listed()
        log.info("TWSE listed companies: %d", len(symbols))
    except Exception as exc:  # noqa: BLE001
        log.warning("TWSE OpenAPI failed: %s", exc)
    try:
        otc = _fetch_tpex_otc()
        symbols += otc
        log.info("TPEX OTC companies: %d", len(otc))
    except Exception as exc:  # noqa: BLE001
        log.warning("TPEX OpenAPI failed: %s", exc)
    if not symbols:
        symbols = _load_tw_sample()
        log.warning("Falling back to bundled TW sample list (%d symbols)", len(symbols))
    # De-duplicate by code, keep first occurrence (listed wins over OTC).
    seen: set[str] = set()
    unique = []
    for s in symbols:
        if s.code not in seen:
            seen.add(s.code)
            unique.append(s)
    return unique


def us_universe() -> list[SymbolInfo]:
    """S&P 500 union Nasdaq 100 from the bundled static lists."""
    tickers = _load_us_list("sp500.txt")
    for t in _load_us_list("nasdaq100.txt"):
        if t not in tickers:
            tickers.append(t)
    return [SymbolInfo(code=t, name=t, market="us", board="us") for t in tickers]


def get_universe(market: str, limit: int | None = None) -> list[SymbolInfo]:
    """Return the scan universe for ``market`` ("tw" or "us")."""
    if market == "tw":
        symbols = tw_universe()
    elif market == "us":
        symbols = us_universe()
    else:
        raise ValueError(f"unknown market: {market}")
    if limit is not None:
        symbols = symbols[:limit]
    return symbols


INDEX_TICKER = {"tw": "^TWII", "us": "^GSPC"}
INDEX_NAME_ZH = {"tw": "加權指數", "us": "S&P 500"}
