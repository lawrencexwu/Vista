# Vista — 選股羅盤 Stock Compass

一副能同時借用很多雙眼睛的羅盤：把八位可量化投資大師的準則，在**同一天、同一檔股票**上一次看個明白，
再加上市場溫度計（馬克斯鐘擺 × 科斯托蘭尼雞蛋）推估市場情緒位置。

> 羅盤上的每一個數字，都是**研究參考**，不是買賣訊號。同一檔台積電，米奈爾維尼的趨勢樣板可能 8/8 滿分、
> 巴菲特的護城河只給 3/7 —— 這個落差本身就是資訊。看懂落差，決策還是你自己的。

## 架構：混合式（為什麼不是純 Screener？）

| 需求 | 承載機制 |
|---|---|
| 開一檔股票就看八個模型的分數與逐條明細 | `pine/stock_compass.pine` 圖上記分板 |
| 掃 watchlist、依模型分數排序篩選 | 同一支腳本餵 **Pine Screener**（付費方案功能） |
| 台股上市櫃**全掃**、每日**固定批次**、八模型**排行前五** | `batch/` + GitHub Actions（TradingView 無法排程批次） |
| 神奇公式**真雙排名**、CANSLIM **真 RS Rating** | `batch/`（Pine 看不到別檔股票，做不了跨市場排名） |
| 市場溫度計（美股） | `pine/market_thermometer.pine`（用現成寬度符號 `INDEX:S5TH`） |
| 市場溫度計（台股，含自算市場寬度） | `batch/`（TradingView 沒有台股寬度資料） |

完整決策矩陣、八模型逐條準則定義、溫度計計算方式：見 **[docs/DESIGN.md](docs/DESIGN.md)**。

## 使用方式

### 1. 圖上記分板（任何個股）
1. TradingView → Pine Editor → 貼上 `pine/stock_compass.pine` → 加到圖表（建議日線）
2. 右上表格：八個模型的「通過 X/N 條」、逐條 ✓✗ 明細（滑鼠停留看準則名）、關鍵值；
   `–` 代表資料缺漏（缺漏記為不通過，並顯示缺幾條，避免把缺資料誤讀成壞公司）
3. 台股／美股通用：基準指數自動切換（TWD → TAIEX，其他 → SPX）

### 2. Pine Screener（排行與篩選）
1. 把上述指標存檔並**加入最愛**
2. 開啟 Pine Screener → 選擇本指標 → 選 watchlist（台股全掃需拆成 ≤1000 檔的清單，可用批次產出的清單匯入）
3. 用「米奈爾維尼 趨勢 %」「葛拉漢 防禦型 %」等欄位排序 → 即得該模型的即時排行；
   「通過模型數(≥60%)」欄可找多模型共振的標的

### 3. 市場溫度計
- 開指數圖（`TWSE:TAIEX`、`SP:SPX`）→ 掛 `pine/market_thermometer.pine`
- 美股保留預設寬度符號 `INDEX:S5TH`；台股請關閉寬度分項（完整台股版由批次每日產出）

### 4. 每日批次（排行前五 + 台股溫度計 + ETF 專區）
- GitHub Actions 於台股、美股收盤後各跑一次，全掃台股上市櫃 + 美股 S&P 500 / Nasdaq 100
- 產出 `reports/YYYY-MM-DD/`：溫度計、八模型排行前五、ETF 專區（向柏格致敬的大盤基準快照）、
  以及 **TradingView 可直接匯入的 watchlist txt**
- 本地執行與資料涵蓋注意事項：見 `batch/README.md`

### 5. 大師圖書館
- [docs/MASTERS.md](docs/MASTERS.md)：十六位大師的核心理念與適用情境——八套可量化模型 +
  馬克斯、科斯托蘭尼、柏格三面情境透鏡，另留五席建議候選待欽點

## Repo 結構

```
pine/stock_compass.pine        八大師記分板 + Pine Screener 引擎（同一支腳本）
pine/market_thermometer.pine   鐘擺 0–100 + 雞蛋階段（指數圖用）
batch/                         每日批次：全掃、評分、真排名、溫度計、報告與 watchlist
docs/DESIGN.md                 機制決策矩陣 + 八模型準則明細（Pine 與批次共用同一套定義）
docs/MASTERS.md                大師圖書館：十六位大師的核心理念與適用情境
reports/                       每日批次產出（由 GitHub Actions 提交）
```

## 免責聲明

本專案所有輸出僅為每日批次更新的研究參考，非投資建議；資料可能缺漏或延遲，使用前請自行驗證。
