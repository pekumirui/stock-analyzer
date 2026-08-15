import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'stock.db'}")

# ---- J-Quants API V2 ----
# V1 (メール/パスワード → idToken) は閉鎖予定。V2はダッシュボード発行のAPIキー方式。
JQUANTS_API_KEY = os.getenv("JQUANTS_API_KEY", "")
JQUANTS_BASE = os.getenv("JQUANTS_BASE", "https://api.jquants.com/v2")

# レートリミット (プラン別: Free=5 / Light=60 / Standard=120 / Premium=500)
RATE_LIMIT_PER_MIN = int(os.getenv("JQUANTS_RATE_LIMIT", "60"))
# /fins/summary は全プラン共通で60req/分の別枠
RATE_LIMIT_FINS_PER_MIN = 60

# ミネルヴィニ・ステージ判定パラメータ
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
