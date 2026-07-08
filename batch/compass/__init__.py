"""選股羅盤 (Stock Compass) daily batch package.

Scans TW (TWSE + TPEX) and US (S&P 500 + Nasdaq 100) universes with
yfinance data, scores every symbol against the eight master-model
checklists defined in docs/DESIGN.md section 3, computes the market
thermometer (DESIGN.md section 4) and writes daily markdown reports plus
TradingView-importable watchlists.

All numbers are daily-batch research references, not investment advice.
"""

__version__ = "0.1.0"

MODELS = [
    "graham",
    "buffett",
    "lynch",
    "greenblatt",
    "canslim",
    "minervini",
    "schloss",
    "templeton",
]

MODEL_NAMES_ZH = {
    "graham": "葛拉漢 防禦型投資人",
    "buffett": "巴菲特 質量護城河",
    "lynch": "林區 PEG 成長分類",
    "greenblatt": "葛林布萊特 神奇公式",
    "canslim": "歐尼爾 CANSLIM",
    "minervini": "米奈爾維尼 趨勢樣板",
    "schloss": "施洛斯 深度價值",
    "templeton": "坦伯頓 極端悲觀點",
}
