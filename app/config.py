import os
from collections.abc import MutableMapping
from pathlib import Path

from jobs.share_paths import share_path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
ISSUER_ALIASES_PATH = CONFIG_DIR / "monthly" / "issuer_aliases.json"
ISSUER_METRICS_PATH = CONFIG_DIR / "monthly" / "issuer_metrics.json"
MONTHLY_VOCAB_PATH = CONFIG_DIR / "monthly" / "monthly_vocab.json"
MONTHLY_ROW_ORDER_PATH = CONFIG_DIR / "monthly" / "monthly_row_order.json"
IR_MONTHLY_PATH = CONFIG_DIR / "monthly" / "ir_monthly.json"
SUPPLY_CHAIN_PATH = CONFIG_DIR / "supply" / "supply_chain.json"
SUPPLY_COMPANIES_PATH = CONFIG_DIR / "supply" / "supply_companies.json"
MATERIAL_STATS_PATH = CONFIG_DIR / "supply" / "material_stats.json"
LARGE_HOLDING_DATE_FIXES_PATH = CONFIG_DIR / "large_holding_date_fixes.json"


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

PUBLIC_SITE_NAME = os.getenv("PUBLIC_SITE_NAME", "株KPI")
PUBLIC_ORIGIN = os.getenv("PUBLIC_ORIGIN", "").rstrip("/")

XBRL_DEST = os.getenv("XBRL_DEST", str(share_path("xbrl")))
# Local taxonomy bundle (KAB-60). Never downloaded at parse time.
XBRL_CACHE_DIR = os.getenv("XBRL_CACHE_DIR", str(BASE_DIR / "data" / "xbrl_taxonomy"))
XBRL_TEXT_MAX_CHARS = int(os.getenv("XBRL_TEXT_MAX_CHARS", "1000"))
# xbrl_parse の ZIP 解析プロセス数（KAB-63）。DB 書き込みは親 1 本。1 で直列
XBRL_PARSE_WORKERS = int(os.getenv("XBRL_PARSE_WORKERS", "2"))
# 毎時の xbrl_parse が、原文の無いパース済み filing を 1 回に何件まで埋めるか（KAB-161）
XBRL_DOCUMENTS_FILL_LIMIT = int(os.getenv("XBRL_DOCUMENTS_FILL_LIMIT", "3000"))
# Unused since KAB-60 (no urlopen). Kept so existing .env still loads.
XBRL_XSD_TIMEOUT = float(os.getenv("XBRL_XSD_TIMEOUT", "8"))

# V1 (メール/パスワード → idToken) は閉鎖予定。V2はダッシュボード発行のAPIキー方式。
JQUANTS_API_KEY = os.getenv("JQUANTS_API_KEY", "")
JQUANTS_BASE = os.getenv("JQUANTS_BASE", "https://api.jquants.com/v2")

# Frankfurter v1（ECB、基準通貨 EUR）。キー不要。
# api.frankfurter.app は api.frankfurter.dev/v1 へ 301 する。
FRANKFURTER_BASE = os.getenv("FRANKFURTER_BASE", "https://api.frankfurter.dev/v1")
FX_SERIES_START = os.getenv("FX_SERIES_START", "1999-01-04")
FX_CHUNK_DAYS = int(os.getenv("FX_CHUNK_DAYS", "366"))

ESTAT_APP_ID = os.getenv("ESTAT_APP_ID", "")
ESTAT_BASE = os.getenv("ESTAT_BASE", "https://api.e-stat.go.jp/rest/3.0/app/json")
# 経産省 生産動態統計の統計表一覧（速報・確報の Excel）。既定の User-Agent は 403 になる
METI_SEIDOU_URL = os.getenv(
    "METI_SEIDOU_URL", "https://www.meti.go.jp/statistics/tyo/seidou/result/ichiran/08_seidou.html")
METI_SEKIYU_KAKUHO_URL = os.getenv("METI_SEKIYU_KAKUHO_URL", "https://www.meti.go.jp/statistics/tyo/sekiyuka/index.html")
METI_SEKIYU_SOKUHO_URL = os.getenv("METI_SEKIYU_SOKUHO_URL", "https://www.meti.go.jp/statistics/tyo/sekiyuso/result.html")
METI_USER_AGENT = "Mozilla/5.0 (compatible; stock-analyzer; +https://kabukpi.com/)"
# 経産省 Excel が取れない日数がこれ以上続いたら material_stats が台帳の診断行を出す（page は summary の failed=）
METI_FAIL_PAGE_DAYS = int(os.getenv("METI_FAIL_PAGE_DAYS", "3"))

# レートリミット (プラン別: Free=5 / Light=60 / Standard=120 / Premium=500)
RATE_LIMIT_PER_MIN = int(os.getenv("JQUANTS_RATE_LIMIT", "60"))
# /fins/summary は全プラン共通で60req/分の別枠。earnings-date も /fins/ なので同じ枠
RATE_LIMIT_FINS_PER_MIN = 60
# Light は PubDate の 5 年窓。端の日が 400 にならないよう 1 週間内側
EARNINGS_DATE_LOOKBACK_DAYS = 5 * 365 - 7

# J-Quants ProdCat (商品区分)。011 内国株券 / 012 優先出資 / 013 REIT / 014 ETF /
# 021 外国株券 / 022 外国REIT / 023 外国ETF / 024 外国株預託証券
RS_PRODUCT_CATEGORIES = ("011",)
EARNINGS_EXCLUDE_PRODUCT_CATEGORIES = ("013", "022")

MONTHLY_NEW_DAYS = 7

STAGE_PARAMS = {
    "rs_min_stage2": 70,
    "low52_margin": 1.30,
    "high52_margin": 0.75,
    "ma200_flat_th": 0.005,
    "ma200_down_th": -0.005,
    "stage1_band": 0.08,
    "stage1_band_days": 60,
    "stage1_band_ratio": 0.7,
    "stage4_near_low": 1.10,
}

EARNINGS_PARAMS = {
    # これ以降の発表は翌営業日の始値から。東証の大引けは 2024-11-05 から 15:30（それまで 15:00）
    "market_close": "15:30",
    "market_close_old": "15:00",
    "market_close_since": "2024-11-05",
    "list_history_days": 800,
    "cum_history_years": 5,
    "single_history_quarters": 12,
    "fy_match_tol_days": 45,       # 前年同期の突合許容日数 (決算期変更・うるう年を吸収)
    "revision_noise_th": 0.001,
    "revision_history_years": 4,   # 修正履歴タブに出す年度数 (本決算の後は来期を含む)
    "revision_source": "jquants",  # 修正履歴のデータ源。jquants | xbrl
}
