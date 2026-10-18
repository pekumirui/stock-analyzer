# XBRL 検証DBと IRBANK ゴールデン一致率

TDnet 短信 iXBRL の正規化層（`xbrl_financials`）を、IRBANK の百万円値とオフラインで突合する。
決算 UI / `statements` / `config/xbrl/` マッピングは触らない。設計の科目セットは [`DESIGN_xbrl.md`](DESIGN_xbrl.md) §7.2。

## 検証用 DB

本番 `stock.db`（リポジトリ直下のシンボリックリンク先 `/workspace/verify-live/stock.db` を含む）には **書かない**。
別ファイルを `DATABASE_URL` に指す。SQLite の絶対パスはスラッシュ 4 本:

```
DATABASE_URL=sqlite:////absolute/path/stock_verify.db
```

`init_db` / `python jobs/migrate.py` は通常どおり同じスキーマを作る（`xbrl_*` を含む）。

parse は `stocks` マスタが無いコードを skip する。検証DBへは **stocks テーブルだけ** コピーする:

```
python tools/xbrl/copy_stocks_for_verify.py --from /path/to/stock.db --to /absolute/path/stock_verify.db
```

`--from`/`--source` は読むだけ。`--to`/`--dest-db` がソースと同じ、または本番 `stock.db` / `verify-live/stock.db` なら拒否する。

## dest は R2 のコピー

`jobs/xbrl/xbrl_parse.py --dest` は ZIP ツリーを読む。共有 R2 マウント（`/home/stock/r2-share/xbrl`）を直接指さず、手元へコピーした verify dest を渡す。

```
# 例: rsync -a /home/stock/r2-share/xbrl/ /tmp/xbrl-verify-dest/
```

XSD キャッシュは `XBRL_CACHE_DIR`（既定は R2 配下）。手元だけなら別ディレクトリを指してよい。

## コマンド列（parse → normalize → 一致率）

```
VERIFY_DB=/absolute/path/stock_verify.db
DEST=/absolute/path/xbrl-verify-dest
GOLDEN=data/xbrl_golden

python tools/xbrl/copy_stocks_for_verify.py --from /path/to/stock.db --to "$VERIFY_DB"

# sqlite 絶対パスは sqlite:/// + /abs/... でスラッシュ 4 本
DATABASE_URL="sqlite:///$VERIFY_DB" python jobs/migrate.py

DATABASE_URL="sqlite:///$VERIFY_DB" XBRL_DEST="$DEST" \
  python jobs/xbrl/xbrl_parse.py --dest "$DEST"

DATABASE_URL="sqlite:///$VERIFY_DB" python jobs/xbrl/xbrl_normalize.py

# ゴールデン: 既定はファイル入力（ネットワーク無し）
python tools/xbrl/fetch_xbrl_golden.py --input /path/to/irbank_or_mcp.json --out-dir "$GOLDEN"
# 任意の live HTTP は --live --url が両方必要。キーはコードに書かない（IRBANK_API_KEY / IRBANK_TOKEN）

python tools/xbrl/xbrl_match_rate.py \
  --db "$VERIFY_DB" \
  --golden-dir "$GOLDEN" \
  --out /tmp/xbrl_match_rate.json
```

ZIP が無いときは下のフィクスチャだけで一致率コマンドを確認する。

## スケール

- ゴールデンの `metrics` は **百万円**（`unit: million_jpy`）。
- `xbrl_financials.value` の `unit` が `JPY` / `yen` / 空 / NULL / `iso4217:JPY`（大小無視）なら円とみなし、比較前に **1e6 で割る**。
- `unit` が百万円を示す場合（`million_jpy`, `JPY_MILLIONS`, `million` を含む、など）は割らない。
- 値の大きさからの推定はしない。`unit` を信じる。

## 許容差

次のどちらかを満たせば一致:

- 絶対差 ≤ 1（百万円）
- 相対差 ≤ 0.1%（`|a-b| / max(|a|, |b|, eps) ≤ 0.001`）

## 欠損（分母から除外）

ゴールデンに数値がある科目で、XBRL 側に行が無い（短信に BS が無い等）ときは **不一致にしない**。
`missing` / `excluded` として分母から外す。レポートは `matched / compared / missing / mismatches` を出す。

同一 code × period_end × 科目に連結と単体があるときは **連結を優先**する。

## 期間（累計どうし）

既存 `statements` と同じく **累計 YTD のみ**を突合する。短信 PL は期首〜当期末の累計。

| 四半期 | ゴールデンの作り方 |
|---|---|
| Q1 | その四半期そのもの（単独=累計） |
| Q2 | 上期累計。IRBANK semiannual の H1、または quarterly 単独の Q1+Q2 |
| Q3 | 9か月累計（単独 Q1+Q2+Q3） |
| FY | 通期 / full_year |

`period_basis` が `"cumulative"` 以外（`single_quarter` / 欠落）のゴールデンは **スキップ**（理由付き。バッチは続行）。IRBANK 四半期単独を短信累計と生比較しない。

詳細は `data/xbrl_golden/README.md`。

## ゴールデン

スキーマは `data/xbrl_golden/README.md` と `data/xbrl_golden/schema.example.json`。
実データの JSON は git に入れない（`.gitignore`）。テスト用は `tests/xbrl/testdata/xbrl_match/`。

IRBANK 指標キー → `xbrl_std_items.code`:

| golden | std_item |
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

## フィクスチャだけで一致率を出す（R2 不要）

```
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

期待する stdout の形:

```
overall: 7/8 = 87.50%  (missing_excluded=1, skipped_non_cumulative=2)
per_metric: net_sales=1/1 ...
```

テスト: `.venv/bin/python tests/xbrl/test_xbrl_match_rate.py`

## J-Quants `statements` との突合（KAB-47）

IRBANK ゴールデンとは別に、`statements`（J-Quants `/v2/fins/summary`）と `xbrl_financials` を突合する。本番 `stock.db` には書かない。検証DBへ stocks に加えて statements をコピーしてよい。

```
python tools/xbrl/copy_stocks_for_verify.py --from /path/to/stock.db --to "$VERIFY_DB" --tables stocks,statements
DATABASE_URL="sqlite:///$VERIFY_DB" python jobs/migrate.py
DATABASE_URL="sqlite:///$VERIFY_DB" python jobs/xbrl/xbrl_normalize.py --rebuild
python tools/xbrl/xbrl_vs_statements.py --db "$VERIFY_DB" --out /tmp/xbrl_vs_statements.json
```

突合は `disclosed_date == date(submitted_at)` の filing だけ。日付が合わない statements は `skipped_date`（全履歴フォールバックしない）。配当は `fiscal_year_end` と `period_end` が同じ行を使う。statements が百万円切捨てで |差|<1e6 なら `rounding`（一致率の分子に入れるが別集計）。

ZIP が無いときは `tests/xbrl/test_xbrl_forecast.py` のフィクスチャでコマンド形を確認する。
