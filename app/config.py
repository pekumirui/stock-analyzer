import os
from collections.abc import MutableMapping
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def apply_dotenv(text: str, environ: MutableMapping[str, str]) -> None:
    """KEY=VALUE を setdefault で載せる。既存の環境変数は上書きしない。"""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if not key or not key.isidentifier():
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        environ.setdefault(key, val)


def load_dotenv(path: Path | None = None, environ: MutableMapping[str, str] | None = None) -> None:
    dest = os.environ if environ is None else environ
    src = BASE_DIR / ".env" if path is None else path
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return
    apply_dotenv(text, dest)


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'stock.db'}")

# TDnet XBRL (parse/normalize). Fetch dest matches scripts/tdnet_xbrl_r2.DEFAULT_DEST.
XBRL_DEST = os.getenv("XBRL_DEST", "/home/stock/r2-share/xbrl")
XBRL_CACHE_DIR = os.getenv("XBRL_CACHE_DIR", "/home/stock/r2-share/xbrl/_cache")
XBRL_TEXT_MAX_CHARS = int(os.getenv("XBRL_TEXT_MAX_CHARS", "1000"))

# V1 (メール/パスワード → idToken) は閉鎖予定。V2はダッシュボード発行のAPIキー方式。
JQUANTS_API_KEY = os.getenv("JQUANTS_API_KEY", "")
JQUANTS_BASE = os.getenv("JQUANTS_BASE", "https://api.jquants.com/v2")

# レートリミット (プラン別: Free=5 / Light=60 / Standard=120 / Premium=500)
RATE_LIMIT_PER_MIN = int(os.getenv("JQUANTS_RATE_LIMIT", "60"))
# /fins/summary は全プラン共通で60req/分の別枠
RATE_LIMIT_FINS_PER_MIN = 60

# J-Quants ProdCat (商品区分)。011 内国株券 / 012 優先出資 / 013 REIT / 014 ETF /
# 021 外国株券 / 022 外国REIT / 023 外国ETF / 024 外国株預託証券
RS_PRODUCT_CATEGORIES = ("011",)  # RS・ステージは普通株同士の相対
EARNINGS_EXCLUDE_PRODUCT_CATEGORIES = ("013", "022")

# 月次 TOP の「新着」。last_ingested_at の JST 日付が今日を含むこの日数。
MONTHLY_NEW_DAYS = 7

STAGE_PARAMS = {
    "rs_min_stage2": 70,        # Stage2に必要なRSランク下限
    "low52_margin": 1.30,       # 52週安値からの上昇率 (30%以上)
    "high52_margin": 0.75,      # 52週高値からの下落許容 (25%以内)
    "ma200_flat_th": 0.005,     # MA200の1ヶ月変化率がこの絶対値未満なら「横ばい」
    "ma200_down_th": -0.005,    # これ未満なら「下降」
    "stage1_band": 0.08,        # Stage1: MA200±8%帯
    "stage1_band_days": 60,     # 判定に使う日数
    "stage1_band_ratio": 0.7,   # 帯域内に収まる日の比率
    "stage4_near_low": 1.10,    # 52週安値の1.1倍以内
}

EARNINGS_PARAMS = {
    "list_history_days": 800,      # 一覧のYoY/QoQ算出に必要な履歴の遡り日数(前年同期+その前四半期を含む)
    "cum_history_years": 5,        # 累計テーブルの行数 = 過去4期 + 当期
    "single_history_quarters": 12,  # 3か月決算テーブルの行数 (トレンドが追える長さ)
    "fy_match_tol_days": 45,       # 前年同期の突合許容日数 (決算期変更・うるう年を吸収)
    "revision_noise_th": 0.001,    # 予想修正とみなす最小変化率 (端数差はノイズ)
}
