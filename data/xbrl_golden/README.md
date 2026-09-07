# XBRL golden store (IRBANK)

IRBANK の財務値を、XBRL 正規化層との突合用に置けるディレクトリ。

- 実データ JSON は git に入れない（`.gitignore`）。手元で `tools/fetch_xbrl_golden.py --input` が書く。
- この README と `schema.example.json` だけ追跡する。
- テスト用の小さな JSON は `scripts/fixtures/xbrl_match/`。

## 単位

`metrics` の値は **百万円**（`unit` は `million_jpy`）。円ではない。
`tools/xbrl_match_rate.py` は `xbrl_financials.unit` が円なら 1e6 で割ってから比較する（`docs/XBRL_VERIFY.md`）。

## ファイル名

発行体 × 期末で 1 ファイル: `{code4}_{period_end}.json`  
例: `7203_2025-03-31.json`

## フィールド

| キー | 内容 |
|---|---|
| `code` | J-Quants 5桁（`"72030"`） |
| `code4` | 表示・TDnet の 4桁（`"7203"`） |
| `period_end` | 会計期間終了日 `YYYY-MM-DD` |
| `accounting_standard` | `JPGAAP` / `IFRS` / `USGAAP` / `null` |
| `is_consolidated` | 連結なら true |
| `metrics` | 下表。値は百万円。欠損キーは比較しない |
| `unit` | 常に `million_jpy` |
| `source` | 既定 `irbank` |
| `fetched_at` | ISO 8601（JST なら `+09:00`） |

`metrics` キー（IRBANK → `xbrl_std_items.code`）:

| metrics | std_item |
|---|---|
| revenue | net_sales |
| operating_income | operating_income |
| ordinary_income | ordinary_income |
| net_income | net_income |
| total_assets | total_assets |
| equity | net_assets |
| total_liabilities | total_liabilities |
| operating_cf | operating_cf |
| investing_cf | investing_cf |
