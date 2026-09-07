# XBRL golden store (IRBANK)

IRBANK の財務値を、XBRL 正規化層との突合用に置けるディレクトリ。

- 実データ JSON は git に入れない（`.gitignore`）。手元で `tools/fetch_xbrl_golden.py --input` が書く。
- この README と `schema.example.json` だけ追跡する。
- テスト用の小さな JSON は `tools/fixtures/xbrl_match/`。

## 期間の前提（累計どうし）

KPI は既存 `statements` と同じく **累計 YTD のみ**。

| ラベル | ゴールデンの意味 |
|---|---|
| Q1 | 当該四半期 = 期首〜Q1 末（単独=累計） |
| Q2 | 上期累計（H1）。IRBANK 四半期単独なら Q1+Q2 を合算 |
| Q3 | 9か月累計。単独なら Q1+Q2+Q3 を合算 |
| FY | 通期 |

TDnet 短信の PL も CurrentYTD（期首〜当期末）の累計。**IRBANK `period_type=quarterly` の Q2/Q3 単独を短信累計と生比較しない。**

`tools/fetch_xbrl_golden.py` は出力に必ず `period_basis: "cumulative"` を付ける。単独のままでは書かない（変換できない行は skip）。

## 単位

`metrics` の値は **百万円**（`unit` は `million_jpy`）。円ではない。
`tools/xbrl_match_rate.py` は `xbrl_financials.unit` が円なら 1e6 で割ってから比較する（`docs/XBRL_VERIFY.md`）。

## ファイル名

発行体 × 期末で 1 ファイル: `{code4}_{period_end}.json`  
例: `7203_2025-09-30.json`（3月期の Q2=上期累計）

## フィールド

| キー | 内容 |
|---|---|
| `code` | J-Quants 5桁（`"72030"`） |
| `code4` | 表示・TDnet の 4桁（`"7203"`） |
| `period_end` | 会計期間終了日 `YYYY-MM-DD` |
| `fiscal_year_end` | 事業年度末（任意）。四半期合算のキー |
| `quarter` | `Q1` / `Q2` / `Q3` / `FY`（累計ラベル） |
| `period_basis` | 必ず `"cumulative"`。`single_quarter` は突合対象外 |
| `accounting_standard` | `JPGAAP` / `IFRS` / `USGAAP` / `null` |
| `is_consolidated` | 連結なら true |
| `metrics` | 下表。値は百万円。欠損キーは比較しない |
| `unit` | 常に `million_jpy` |
| `source` | 既定 `irbank` |
| `fetched_at` | ISO 8601（JST なら `+09:00`） |
| `cumulative_build` | 任意。`q1_equals_ytd` / `sum_q1_q2_standalone` / `api_h1_cumulative` など |

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

## IRBANK Connect 入力の意味

`get_financials` の `period_type`:

| 値 | 各行の意味 |
|---|---|
| `annual` | 通期累計（+ BS） |
| `quarterly` | **単四半期** Q1〜Q4 + `full_year`（BS は full_year のみ） |
| `semiannual` | **H1=上期累計** + `full_year`（CF を半期のみ出す企業向け） |

fetch は quarterly の単独行を読み、Q2/Q3 を累計に積み上げてからゴールデンを書く。
