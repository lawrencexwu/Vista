# batch/ — 選股羅盤每日批次（Stock Compass daily batch）

外部每日批次：掃描台股（上市＋上櫃）與美股（S&P 500 + Nasdaq 100），
用 yfinance 資料對八位大師模型逐條打分（準則定義見 `docs/DESIGN.md` §3），
計算市場溫度計（§4），輸出每日報告與 TradingView 可匯入的 watchlist。

> 所有數字都是每日批次更新的研究參考，不是投資建議。

## 本機執行

```bash
cd batch
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 冒煙測試（只掃前 15 檔）
python -m compass.run --market us --limit 15

# 完整掃描
python -m compass.run --market tw
python -m compass.run --market us

# 指定報告／快取日期
python -m compass.run --market us --date 2026-07-08
```

輸出位置（repo 根目錄）：

```
reports/YYYY-MM-DD/
├── report_tw.md / report_us.md     # 溫度計 + 八模型排行前五 + 通過統計
└── watchlists/<model>_{tw,us}.txt  # TradingView 匯入格式，一行一檔
```

離線邏輯測試（完全不需要網路，合成資料驗證八模型／排名／溫度計／報告）：

```bash
cd batch
python -m pytest tests/ -q        # 或
python tests/test_models.py
```

## 快取機制

- 原始資料以 pickle 存在 `batch/.cache/<date>/`（已被 gitignore）：
  - `prices_<market>_3y.pkl`：整批價格（3 年日線，`yf.download` 分塊＋重試）
  - `index_<ticker>.pkl`：指數歷史（6 年，溫度計需要 5 年百分位）
  - `fund_<ticker>.pkl`：每檔的 `info` ＋ 年度／季度三大報表＋股利史
- 快取以「日期」為 key：同一天重跑幾乎不再打網路（中斷續跑很便宜）；
  換一天自動重抓。舊日期目錄可隨手整個刪掉。

## 資料覆蓋注意事項（yfinance 盡力而為）

- **缺資料＝該條不通過，但另列為「資料缺漏」（na）**。報告的「未過條目」
  與「資料缺漏」分開兩欄，避免把缺資料誤讀成壞公司（DESIGN.md §2）。
  百分比分母仍是全部條數，所以缺漏會拉低排序 —— 這是刻意的保守設計。
- **台股基本面**：yfinance 對台股中小型股的財報欄位常缺（尤其上櫃），
  基本面型模型（葛拉漢、巴菲特、施洛斯…）在台股會有較多 na；
  價格型模型（米奈爾維尼、坦伯頓價格條、CANSLIM 技術條）覆蓋率高。
- **年度財報史約 4 年**：yfinance 只給約 4 個年度，所以「10 年盈餘紀錄」
  「5 年／10 年 P/E 中位數」等長期條件是以可得年數遞減後的近似
  （葛拉漢 G4/G6、坦伯頓 P3/P5、林區 L4）。P/E 歷史用「各會計年度收盤價
  ÷ 該年度 EPS」重建，至少要 3 個點才評估，否則記 na。
- **價格史 3 年**：坦伯頓 P1/P2 的「10 年高低點」以可得最長（約 3 年）代替，
  DESIGN.md 的「或可得最長」語意；比 10 年版寬鬆，判讀時留意。
- **股利判定**：股利史為空序列＝「從未配息」（不通過）；抓不到序列＝資料缺漏。
- **RS Rating 是「掃描範圍內」的真百分位**：用 `--limit` 冒煙測試時，
  百分位只在被掃到的小樣本內計算，數字沒有全市場意義。
- **網路失敗容錯**：TWSE／TPEX 清單抓不到時退回內建樣本清單
  （`compass/data/tw_sample.txt`，約 40 檔大型股）；yfinance 完全連不上時
  批次仍會跑完並輸出報告，掃描統計會標注無價格資料。
- 美股成分股清單是靜態快照（`compass/data/sp500.txt`、`nasdaq100.txt`），
  偶爾手動更新：`python scripts/refresh_universe.py`（抓 Wikipedia）。

## 排名與 tiebreak（rank.py）

- **神奇公式**：全市場對 EBIT/EV 與 ROIC 各自排名（高者名次小），名次相加
  後由小到大 —— 葛林布萊特的排行前五就是這個綜合排名，不是 2 條門檻的百分比。
- **其他模型前五**：通過 % 由高到低，同分依模型相關指標：
  葛拉漢→P/E×P/B 低者先；巴菲特→ROE 高者先；林區→PEG 低者先；
  CANSLIM／米奈爾維尼→6 個月 RS 百分位高者先；施洛斯→P/B 低者先；
  坦伯頓→自高點回檔深者先。缺指標者同分組內墊底。

## 匯入 TradingView watchlist

1. 開 TradingView 右側 Watchlist 面板 → 右上「…」選單 → **Import list…**
2. 選 `reports/<date>/watchlists/<model>_<market>.txt`（格式即
   `TWSE:2330`／`TPEX:5483`／`NASDAQ:AAPL` 一行一檔，可直接匯入）。
3. 匯入後開啟 `pine/stock_compass.pine` 記分板逐檔看八模型逐條明細。

美股交易所前綴由 yfinance `info["exchange"]` 對映（NMS→NASDAQ、NYQ→NYSE、
ASE→AMEX…），無法辨識時保守預設 NASDAQ。

## GitHub Actions 排程

`.github/workflows/daily-compass.yml`：

- 週一至週五 10:00 UTC（台北 18:00，台股收盤後）跑 `--market tw`
- 週一至週五 21:30 UTC（美股收盤後）跑 `--market us`
- 也可 `workflow_dispatch` 手動指定市場
- 產出 commit 回當前分支：`chore(reports): daily compass <date> <market> [skip ci]`，
  push 失敗時 pull --rebase 重試（兩個排程可能互相搶 push）。
