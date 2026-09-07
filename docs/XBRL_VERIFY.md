# XBRL 検証DBと IRBANK ゴールデン一致率

TDnet 短信 iXBRL の正規化層（`xbrl_financials`）を、IRBANK の百万円値とオフラインで突合する。
決算 UI / `statements` / `config/xbrl/` マッピングは触らない。設計の科目セットは [`DESIGN_xbrl.md`](DESIGN_xbrl.md) §7.2。

## 検証用 DB

本番 `stock.db`（リポジトリ直下のシンボリックリンク先 `/workspace/verify-live/stock.db` を含む）には **書かない**。
別ファイルを `DATABASE_URL` に指す。SQLite の絶対パスはスラッシュ 4 本:

```
DATABASE_URL=sqlite:////absolute/path/stock_verify.db
```

`init_db` / `python scripts/migrate.py` は通常どおり同じスキーマを作る（`xbrl_*` を含む）。

parse は `stocks` マスタが無いコードを skip する。検証DBへは **stocks テーブルだけ** コピーする:

```
python scripts/copy_stocks_for_verify.py --from /path/to/stock.db --to /absolute/path/stock_verify.db
```

`--from`/`--source` は読むだけ。`--to`/`--dest-db` がソースと同じ、または本番 `stock.db` / `verify-live/stock.db` なら拒否する。

## dest は R2 のコピー

`scripts/xbrl_parse.py --dest` は ZIP ツリーを読む。共有 R2 マウント（`/home/stock/r2-share/xbrl`）を直接指さず、手元へコピーした verify dest を渡す。

```
# 例: rsync -a /home/stock/r2-share/xbrl/ /tmp/xbrl-verify-dest/
```

XSD キャッシュは `XBRL_CACHE_DIR`（既定は R2 配下）。手元だけなら別ディレクトリを指してよい。

## コマンド列（parse → normalize → 一致率）

```
VERIFY_DB=/absolute/path/stock_verify.db
DEST=/absolute/path/xbrl-verify-dest
GOLDEN=data/xbrl_golden

python scripts/copy_stocks_for_verify.py --from /path/to/stock.db --to "$VERIFY_DB"

# sqlite 絶対パスは sqlite:/// + /abs/... でスラッシュ 4 本
DATABASE_URL="sqlite:///$VERIFY_DB" python scripts/migrate.py

DATABASE_URL="sqlite:///$VERIFY_DB" XBRL_DEST="$DEST" \
  python scripts/xbrl_parse.py --dest "$DEST"

DATABASE_URL="sqlite:///$VERIFY_DB" python scripts/xbrl_normalize.py

# ゴールデン: 既定はファイル入力（ネットワーク無し）
python tools/fetch_xbrl_golden.py --input /path/to/irbank_or_mcp.json --out-dir "$GOLDEN"
# 任意の live HTTP は --live --url が両方必要。キーはコードに書かない（IRBANK_API_KEY / IRBANK_TOKEN）

python tools/xbrl_match_rate.py \
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

## ゴールデン

スキーマは `data/xbrl_golden/README.md` と `data/xbrl_golden/schema.example.json`。
実データの JSON は git に入れない（`.gitignore`）。テスト用は `scripts/fixtures/xbrl_match/`。

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
root = Path("scripts/fixtures/xbrl_match")
db = Path(tempfile.mkdtemp()) / "fixture.db"
sqlite3.connect(db).executescript((root / "seed.sql").read_text(encoding="utf-8"))
print(db)
PY
)

.venv/bin/python tools/xbrl_match_rate.py \
  --db "$DB" \
  --golden-dir scripts/fixtures/xbrl_match
```

期待する stdout の形:

```
overall: 7/8 = 87.50%  (missing_excluded=1)
per_metric: net_sales=1/1 ...
```

テスト: `.venv/bin/python scripts/test_xbrl_match_rate.py`
