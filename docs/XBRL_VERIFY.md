# XBRL 正規化のオフライン検証

`xbrl_financials` と `data/xbrl_golden/`（IRBANK）の突合。**本番 `stock.db` には書かない**（symlink でも不可）。マッピング YAML に科目を独断で足さない（`docs/DESIGN_xbrl.md` §7.2）。

## 1. verify DB

```bash
.venv/bin/python tools/xbrl_setup_verify_db.py
# --db stock_verify.db  --source-db stock.db（stocks だけ読む。source は変更しない）
```

`DATABASE_URL=sqlite:///…/stock_verify.db` で `init_db()` し、`xbrl_*` を作ってから stocks をコピーする。source に stocks が無ければ非ゼロ終了。

## 2. parse / normalize（ZIP があるとき）

R2 のサンプル ZIP を `--dest` に向ける。ジョブは verify DB だけを見る。

```bash
export DATABASE_URL="sqlite:///$(pwd)/stock_verify.db"
.venv/bin/python scripts/xbrl_parse.py --dest /path/to/xbrl-sample
.venv/bin/python scripts/xbrl_normalize.py
```

`--dest` の実体はインフラ側が R2 mount や手元コピーで渡す。無いときは parse を飛ばして golden 検査だけできる。

## 3. golden

`72030` / `68610` / `99840` の通期+Q1 はシード済み。再取得は IRBANK Connect を外部で行い、必要なら `tools/xbrl_fetch_golden.py --from-payload` で JSON を書き直す。形式は `data/xbrl_golden/README.md`。

```bash
.venv/bin/python tools/xbrl_fetch_golden.py
```

## 4. match_rate（ネットワーク無し）

```bash
.venv/bin/python tools/xbrl_match_rate.py
.venv/bin/python tools/xbrl_match_rate.py --db stock_verify.db --golden-dir data/xbrl_golden --tolerance 1
.venv/bin/python tools/xbrl_match_rate.py --codes 72030 68610
```

結合: 同じ `code`、連結優先 (`is_consolidated=1`)。通期は `period_end` の年月 = `fiscal_year` (YYYY/MM)。四半期は filing/financial の `period_type` を Q1/Q2/Q3 に合わせ、`fiscal_year_end` または `period_end` の年月で FY を見る。`full_year` golden はスキップ。

終了コードは成功なら常に 0（一致率 100% 未満でも）。IO / スキーマエラーだけ非ゼロ。

## 5. 許容差

DB の金額を百万円整数にしてから比較する。

- unit が `JPY` / `Yen` / 空 → `value / 1e6`
- 既に `million_jpy` → そのまま
- 四捨五入して整数百万円
- `abs(db - golden) <= --tolerance`（既定 1）

片方欠損は `missing`（分母から除外）。golden の null はスキップ。

`match_rate = matches / (matches + mismatches)`

## 6. マッピング

一致率が低いときに `config/xbrl/mappings/*.yaml` へ科目を足さない。`report-unmapped` を出して確認してから。
