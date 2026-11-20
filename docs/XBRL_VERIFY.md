# XBRL 検証 DB とゴールデン一致率

TDnet 短信 iXBRL の正規化層（`xbrl_financials`）を、本番用の `stock.db` を汚さずに別ファイルへ取り込み、IR BANK のゴールデンデータや J-Quants の `statements` とオフラインで突き合わせる仕組みと手順をまとめる。

標準科目の定義と正規化ロジックは [XBRL の設計](jobs/xbrl.md) を参照する。

## 検証 DB と入出力の構成

検証時は本番の `stock.db`（および `/workspace/verify-live/stock.db`）には一切書き込まず、環境変数 `DATABASE_URL` に別の SQLite ファイルを指定する（絶対パスの場合は `sqlite:////absolute/path/stock_verify.db` のようにスラッシュを 4 本続ける）。

| 要素 | パス・スクリプト | 役割 |
|---|---|---|
| マスタ複製 | `tools/xbrl/copy_stocks_for_verify.py` | 元の `stock.db` を読み取り専用（`?mode=ro`）で開き、`stocks`（`--tables stocks,statements` 指定時は `statements` も）だけを検証 DB へコピーする。コピー元と同じパスや `./stock.db`・`verify-live/stock.db` への出力は拒否する |
| タクソノミキャッシュ | `XBRL_CACHE_DIR`（既定 `data/xbrl_taxonomy`） | パース時に参照するローカルのタクソノミ置き場（ネットワーク通信は行わない。更新は `tools/xbrl/taxonomy_bundle.py`） |
| ゴールデン作成・配置 | `tools/xbrl/fetch_xbrl_golden.py`、`data/xbrl_golden/` | IR BANK 等の JSON から検証用ゴールデン JSON を作る（実データは `.gitignore` 対象。スキーマ例は `data/xbrl_golden/schema.example.json`、テスト用フィクスチャは `tests/xbrl/testdata/xbrl_match/`） |
| IR BANK 突合 | `tools/xbrl/xbrl_match_rate.py` | 検証 DB の `xbrl_financials` とゴールデン JSON（百万円単位・累計）を比較し、一致率と不一致明細を出力する |
| `statements` 突合 | `tools/xbrl/xbrl_vs_statements.py` | 同一 DB 内の `statements`（J-Quants）と `xbrl_financials` を開示単位・35 項目で突き合わせる |

## IR BANK ゴールデンとの一致率（`xbrl_match_rate.py`）

`tools/xbrl/xbrl_match_rate.py` は、ゴールデン JSON の `(code, period_end, metric)` ごとに `xbrl_financials` の累計実績を引き、以下の 9 科目を比較する。同じ銘柄・期末・科目に連結と単体の両方がある場合は連結を優先する。

| ゴールデンのキー | 対応する `xbrl_std_items.code` | 科目 |
|---|---|---|
| `revenue` | `net_sales` | 売上高 |
| `operating_income` | `operating_income` | 営業利益 |
| `ordinary_income` | `ordinary_income` | 経常利益 |
| `net_income` | `net_income` | 純利益 |
| `total_assets` | `total_assets` | 総資産 |
| `equity` | `net_assets` | 純資産 |
| `total_liabilities` | `total_liabilities` | 負債合計 |
| `operating_cf` | `operating_cf` | 営業 CF |
| `investing_cf` | `investing_cf` | 投資 CF |

比較時の期間・単位・許容誤差の規則は以下のとおりである（詳細は `data/xbrl_golden/README.md`）。

| 項目 | 規則 |
|---|---|
| 期間基準（累計のみ） | 短信 PL と同じ期首からの累計（`period_basis: "cumulative"`。Q1 は第 1 四半期、Q2 は上期累計、Q3 は 9 か月累計、FY は通期）だけを比較する。四半期単独（`single_quarter` 等）や `period_basis` 欠落のゴールデン行は生比較せず、`skipped_non_cumulative` としてスキップする |
| 単位換算 | ゴールデンの値は百万円（`million_jpy`）。`xbrl_financials.unit` が円系（空文字・NULL・`jpy`・`yen`・`円`・`iso4217:jpy`・`iso4217/jpy`、大小無視）なら比較前に `1e6` で割り、百万円系（`million`・`jpy_millions`・`百万円`・`millions` を含む）なら割らない。桁数からの推測は行わない |
| 一致判定 | 絶対差が `1`（百万円）以下、**または**相対差 `|a - b| / max(|a|, |b|, 1e-9)` が `0.1%`（`0.001`）以下であれば `match` とする |
| 欠損の扱い | ゴールデンに値があり XBRL 側に行が無い場合（短信に BS が無い場合など）は不一致にせず、`missing`（`missing_excluded`）として一致率の分母 `compared` から外す |

## J-Quants `statements` との突合（`xbrl_vs_statements.py`）

IR BANK ゴールデンとは別に、`statements`（J-Quants `/v2/fins/summary`）と `xbrl_financials` を全開示ベースで突き合わせる。まず開示番号の末尾 14 桁（`statements.disclosure_no` と `xbrl_filings.doc_id`）で紐づけ、一致しなければ `code` と開示日（`disclosed_date == date(submitted_at)`）を使う。どちらでも対応する XBRL が見つからない行は `skipped_date` とし、別日の開示へはフォールバックしない。

比較対象は合計 35 項目である（両方とも円または円/株のまま比較し、予想レンジの上下限と前回予想 `forecast_previous` は除外する）。

- `statements` テーブルの列（15 項目）: 当期実績 5 項目（`net_sales`、`operating_profit`、`ordinary_profit`、`profit`、`eps`）、通期予想 5 項目（`fy_*`）、第 2 四半期累計予想 5 項目（`f2_*`）
- `raw_json` 内の項目（20 項目）: 希薄化後 EPS（`DEPS`）、四半期・期末・年間配当実績（`Div1Q`〜`DivAnn`）、配当予想（`FDiv1Q`〜`FDivAnn`）、および非連結の通期・2Q 予想 10 項目（`FNCSales*`〜`FNCEPS2Q`）

連結・単体の選択は原則として連結優先だが、`FNC*`（非連結予想）の 10 項目は単体（`is_consolidated = 0`）を優先する。配当（`dps`）は `statements.fiscal_year_end` と `xbrl_financials.period_end` が一致する行だけを比較する。

| 判定ステータス | 条件と集計上の扱い |
|---|---|
| `match` | 金額項目で絶対差 `1` 円以下、1 株当たり項目で絶対差 `0.05` 円以下、または相対差 `0.1%` 以下（分子 `matched` に算入） |
| `rounding` | `match` に入らない金額項目で、`statements` 側が百万円単位（`1,000,000` の倍数）かつ `|差| < 1,000,000` 円であり、`xbrl_financials` の百万円切り捨て（`ROUND_DOWN`）または四捨五入（`ROUND_HALF_UP`）と一致するもの（分子 `matched` に含めつつ `rounding=` に別掲） |
| `mismatch` | 上記を超える数値差（分母 `compared` に含め不一致とする） |
| `missing` / `skipped` | XBRL 側に行が無い場合は `missing`、`statements` 側が空欄の場合は `skipped` とし、いずれも分母から除外する |

本番環境では MCP の `xbrl_diff_summary`（`source=tdnet`）でも期末ごとの最新書類どうしを比較できる。TDnet の訂正短信が後の開示で過去期の数字を出し直したケース（J-Quants は古い期の訂正を持たない）は `restated` に数え、一致率の分母には入れない。

## 検証手順

### 1. ZIP ツリーからのパース・正規化・一致率計測

元の `stock.db` から `stocks` を検証 DB へコピーし、ZIP ツリー（`data/xbrl` またはその複製）をパース・正規化して一致率を出す。

```bash
VERIFY_DB=/absolute/path/stock_verify.db
DEST=/absolute/path/xbrl-verify-dest
GOLDEN=data/xbrl_golden

.venv/bin/python tools/xbrl/copy_stocks_for_verify.py --from /path/to/stock.db --to "$VERIFY_DB"

# SQLite の絶対パスは sqlite:/// + /absolute/... でスラッシュ 4 本
DATABASE_URL="sqlite:///$VERIFY_DB" .venv/bin/python jobs/migrate.py
DATABASE_URL="sqlite:///$VERIFY_DB" XBRL_DEST="$DEST" \
  .venv/bin/python jobs/xbrl/xbrl_parse.py --dest "$DEST"
DATABASE_URL="sqlite:///$VERIFY_DB" .venv/bin/python jobs/xbrl/xbrl_normalize.py

# ゴールデン JSON の生成（既定はオフラインのファイル入力。ライブ取得は --live と --url の両方が必要）
.venv/bin/python tools/xbrl/fetch_xbrl_golden.py --input /path/to/irbank_or_mcp.json --out-dir "$GOLDEN"

# IR BANK ゴールデンとの一致率計測（--out の拡張子が .json なら JSON、.csv なら CSV で保存）
.venv/bin/python tools/xbrl/xbrl_match_rate.py \
  --db "$VERIFY_DB" \
  --golden-dir "$GOLDEN" \
  --out /tmp/xbrl_match_rate.json
```

### 2. `statements` との 35 項目突合

`stocks` とあわせて `statements` も検証 DB へコピーし、`xbrl_vs_statements.py` を実行する（`--code` で 5 桁銘柄コード 1 社に絞ることも可能）。ZIP が手元に無い環境では `tests/xbrl/test_xbrl_forecast.py` のフィクスチャで動作確認できる。

```bash
.venv/bin/python tools/xbrl/copy_stocks_for_verify.py \
  --from /path/to/stock.db \
  --to "$VERIFY_DB" \
  --tables stocks,statements

DATABASE_URL="sqlite:///$VERIFY_DB" .venv/bin/python jobs/migrate.py
DATABASE_URL="sqlite:///$VERIFY_DB" .venv/bin/python jobs/xbrl/xbrl_normalize.py --rebuild

.venv/bin/python tools/xbrl/xbrl_vs_statements.py \
  --db "$VERIFY_DB" \
  --out /tmp/xbrl_vs_statements.json
```

### 3. テストフィクスチャ単体での動作確認（ZIP 不要）

本番の ZIP が無い手元環境でも、`tests/xbrl/testdata/xbrl_match/` のシード SQL とゴールデンを使って `xbrl_match_rate.py` の出力を確認できる（自動テストは `.venv/bin/python -m pytest tests/xbrl/test_xbrl_match_rate.py`）。

```bash
DB=$(.venv/bin/python - <<'PY'
import sqlite3, tempfile
from pathlib import Path
root = Path("tests/xbrl/testdata/xbrl_match")
db = Path(tempfile.mkdtemp()) / "fixture.db"
sqlite3.connect(db).executescript((root / "seed.sql").read_text(encoding="utf-8"))
print(db)
PY
)

.venv/bin/python tools/xbrl/xbrl_match_rate.py \
  --db "$DB" \
  --golden-dir tests/xbrl/testdata/xbrl_match
```

標準出力には `overall: 7/8 = 87.50%  (missing_excluded=1, skipped_non_cumulative=2)` と科目別の内訳 `per_metric:` が出力される。
