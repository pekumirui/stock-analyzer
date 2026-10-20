from __future__ import annotations

DEFAULT_BASE_URL = "https://api.edinet-fsa.go.jp/api/v2"
API_KEY_ENV = "EDINET_API_KEY"
DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 120.0
DEFAULT_BACKOFF_BASE = 2.0
DEFAULT_BACKOFF_MAX = 60.0
DEFAULT_JITTER = 1.0
RATE_LIMIT_FLOOR = 10.0
MAINTENANCE_RETRY_CAP = 1
USER_AGENT_SUFFIX = "(+python-httpx)"
JST_TZ_NAME = "Asia/Tokyo"

EDINET_CODE_LIST_JA = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
EDINET_CODE_LIST_EN = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelisteng/Edinetcode.zip"
FUND_CODE_LIST_JA = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Fundcode.zip"
FUND_CODE_LIST_EN = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelisteng/Fundcode.zip"
CODE_AGGREGATION_CSV = "https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140190.csv"

DOC_ID_PATTERN = r"^[A-Z0-9]{8}$"
DATE_MAX_LOOKBACK_DAYS = 10 * 365 + 7
