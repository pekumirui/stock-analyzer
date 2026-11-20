# TDnet XBRL 財務データ蓄積 設計書（stock-analyzer 向け）

正本の置き場は本ファイル。全体は [`docs/OVERVIEW.md`](../OVERVIEW.md)。実装時の約束は [`AGENTS.md`](../../AGENTS.md)。

不明点は推測で仕様を変えない。特にマッピング定義は業務知識に依存するため、§7.2 に無い科目を独断で足さない。

---

## ほかの取込との関係

TDnet 短信 XBRL は取得済み ZIP（`jobs/xbrl/tdnet_xbrl_r2.py`）を `xbrl_filings` / `xbrl_facts` / `xbrl_financials` に載せる。
スキーマとジョブは [`docs/jobs/xbrl.md`](xbrl.md)。EDINET の提出者・書類一覧は [`docs/jobs/edinet.md`](edinet.md)。決算速報 UI が読むのは `statements`。短信 XBRL から同じ形の行を作って入れ、数値は XBRL を優先する（KAB-383。`docs/jobs/xbrl.md` §3.2）。
決算一覧の売上総利益は `idx_xbrl_financials_gp_cover` と `idx_xbrl_filings_id_cover` で表本体を読まない（KAB-295）。
有報・短信 XBRL の表示リンク（`*pre.xml`）のうち財務3表の区分と、その要素の並びは `xbrl_presentation_roles`（書類 × 区分で 1 行。取り込みで入れ替え、`xbrl_filings.presentation_status` に済みの印を付ける。過去分は `jobs/xbrl/xbrl_presentation.py` を 1 回流す。`docs/jobs/xbrl.md` §4.4・§4.5）。
短信 PDF だけの期の行と XBRL の要素の対応は `xbrl_pdf_matches`（PDF の fact 1 つに 1 行。付け直しは `tools/tdnet_pdf/matches.py`）、有報・短信 XBRL・短信 PDF を要素名で並べる集計用のビューは `xbrl_statement_values`（`docs/jobs/xbrl.md` §18）。表と索引（`xbrl_elements` の `local_name` と `'ext:' || local_name`）は `init_db` / `jobs/migrate.py` が足す。ビューも `init_db` が作り、定義の文字列が変わっていれば作り直す。リポジトリのビューはこれだけ。


## 0. 確定事項（2026-09-07）

実装前に確認済み。覆すときはこの節と該当箇所を両方直す。

1. 第1弾の取得・パース対象は **TDnet 短信 iXBRL のみ**。EDINET の有報・半期報は KAB-73 で `source='edinet'` として足した（取得・対象・fact の絞り込みは `docs/jobs/edinet.md`「有報・半期報の財務 XBRL」）。パース・正規化・マッピングは同じものを使う。
2. 企業キーは `stocks.code`（5桁）だけ。`stocks.edinet_code`（nullable）は KAB-72 で足した（`docs/jobs/edinet.md`「提出者」）。XBRL 層のキーには使わない。
3. context から連結/単体が取れなければ **連結として扱う**（`is_consolidated=1`）。
4. 生データ・正規化層は **既存と同じ SQLite**（`DATABASE_URL` / 本番 `stock.db`）に載せる。分離は容量が問題になってから。
5. 決算速報 UI / API が読むのは **`statements`**。短信 XBRL から同じ形の行を開示番号で作って入れる（KAB-383。§3.2）。例外として、決算表の修正履歴（KAB-57）は `?rev_source=xbrl` / `EARNINGS_PARAMS["revision_source"]` で `xbrl_financials` を読める（既定は `jquants`）。読むだけで `statements` は変えない。修正履歴が読むのは `source='tdnet'` の filing だけ。

---

## 1. 目的とスコープ

### 1.1 目的

- `data/xbrl` に溜まっている TDnet XBRL ZIP をパースし、fact を欠損なく保存する（生データ層）
- 企業横断で比較できる標準勘定科目に正規化する（正規化層）
- 既存の `statements`（短信サマリ）を置き換えず、より厚い BS/PL/CF の基盤にする

### 1.2 スコープ内（第1弾）

| 項目 | 内容 |
|---|---|
| 取得元 | TDnet。取得自体は既存ジョブ。本設計の対象は **パース以降** |
| 入力 | `data/xbrl/{code4}/` の ZIP と `manifest/{YYYY-MM-DD}.jsonl`。読むときは MCP `fs_*`（`root=data`）か box の `/mnt/vps/data` |
| 対象書類 | 決算短信・四半期短信・訂正短信の **Summary iXBRL**（本表） |
| 対象データ | iXBRL の fact を保存。1000 文字超のテキストブロックは値を捨てるが要素は残す |
| 正規化対象 | BS / PL / CF の §7.2 最小セット + EPS / 配当 / 業績予想（KAB-47） |
| 企業キー | `stocks.code`（5桁）。ZIP 側の 4桁は末尾 `0` を付けて照合 |
| DB | 既存 `DATABASE_URL` |

### 1.3 スコープ外（第1弾）

- EDINET 有報・四半期報告書の取得とパース（有報・半期報は KAB-73 で足した。`docs/jobs/edinet.md`「有報・半期報の財務 XBRL」）
- AuditDoc / 監査報告書
- セグメント軸付き fact の正規化層投入（KAB-77 で別表 `xbrl_segments` に入れた。§14）
- `statements` との自動突合・置換
- インフラファンド専用の科目体系

---

## 2. アーキテクチャ

```
[TDnet inbs ZIP]
        │
        │ 既存: jobs/xbrl/tdnet_xbrl_r2.py
        ▼
[data/xbrl/{code4}/ + manifest/*.jsonl]
        │
        │ (2) parse   jobs/xbrl/xbrl_parse.py
        ▼
┌──────────────────────────────────────┐
│ 生データ層                            │
│  xbrl_filings / xbrl_contexts         │
│  xbrl_elements / xbrl_facts           │
└──────────────────────────────────────┘
        │
        │ (3) normalize   jobs/xbrl/xbrl_normalize.py
        ▼
┌──────────────────────────────────────┐
│ 正規化層  xbrl_financials             │
└──────────────────────────────────────┘

[J-Quants /v2/fins/summary] ──既存──▶ statements ◀── (4) xbrl_statements（短信 XBRL。数値は XBRL を優先）  ← 決算速報 UI はここ
[stocks] ──既存──▶ 銘柄マスタ（5桁）。XBRL 側の FK 相当は code 文字列照合のみ
```

パースが読むのは `data/xbrl`。FUSE `/home/stock/r2-share` は残置（ジョブの dest ではない）。

3 ジョブは独立して再実行できる。cron は毎時 :00 に (1)→(2)→(3) を 1 行で続けて走らせるが、各スクリプトは今までどおり単独で呼べる。

| ジョブ | 実装 | 責務 |
|---|---|---|
| (1) fetch | `jobs/xbrl/tdnet_xbrl_r2.py`（既存・変更最小） | 一覧（TDnet の開示一覧）→ ZIP → dest（既定 `data/xbrl`）。skip 正本は manifest の `xbrl_id` |
| (2) parse | `jobs/xbrl/xbrl_parse.py`（新規） | ZIP → xbrlp → `xbrl_*` 生データ |
| (3) normalize | `jobs/xbrl/xbrl_normalize.py`（新規） | facts + YAML マッピング → `xbrl_financials` |

テーブル名に `xbrl_` を付ける。既存の `statements` / 月次 `*_elements` と衝突させない。

---

## 3. 既存データとの接続

### 3.1 企業の識別

`stocks` に EDINET コードも法人番号も無い。XBRL 側の企業キーは **証券コードだけ**。

```
TDnet company_code / 保存ディレクトリ {code4}
        │  to_code4 + 末尾 0   （既存 tdnet_xbrl_r2.jq_lookup_code と同じ）
        ▼
stocks.code   例 7203 → 72030、262A → 262A0
```

- `stocks` に無いコードの ZIP は今日 `_unmatched/` に落ちている。parse 対象にしない（第1弾）。
- インフラファンドも `_unmatched/`。第1弾はスキップ。
- `stocks.edinet_code` は KAB-72 で nullable で足した（§0-2）。EDINET の書類は `edinet_documents.code` で `stocks.code` に付いている。

### 3.2 `statements` との関係

| | `statements` | `xbrl_financials` |
|---|---|---|
| ソース | J-Quants 短信サマリと、短信 XBRL から作った行（`source`） | TDnet XBRL 生 fact を正規化 |
| 形 | 横持ち（売上・営利・予想など固定列） | 縦持ち（標準科目 × 値） |
| UI | 決算速報・詳細の正本 | 決算表の修正履歴だけデータ源に選べる（既定は `statements`） |
| 期間 | 累計実績 + 会社予想 | 本表の当期実績に加え、EPS・配当・会社予想（通期/中間、レンジ、予想修正） |

**XBRL から `statements` の行を作る（KAB-383。`app/xbrl/statements.py`、`jobs/xbrl/xbrl_statements.py`）**: J-Quants の取込は 19:30 の 1 回だけで、短信 XBRL は毎時入る。決算速報を早く出すため、TDnet の解析済み filing（決算短信・業績予想の修正・配当予想の修正）から J-Quants と同じ形の行を作る。

- キーは開示番号（`doc_id` の後ろ 14 桁）。J-Quants の `disclosure_no` と同じで、開示日時も一致する（2026-10 以降の 1,829 件すべて）
- 数値は XBRL を優先する。XBRL に値がある列は上書きし、無い列は既にある J-Quants の値を残す。行は `source='xbrl'`
- J-Quants の取込は `source='xbrl'` の行の数値を変えない。`raw_json` を J-Quants のものにし、空の数値だけ埋める。XBRL が無い開示（`stocks` に無い銘柄・取込漏れ）は今までどおり J-Quants の行（`source='jquants'`。KAB-383 より前の行は NULL）
- 列の作り方: 実績は §6.4 の当期実績（連結があれば連結）。期首は累計実績の期首、年度末は同じ期首の通期予想の期末（無ければ期首から 1 年）。本決算以外の `fy_*` は当期の通期予想、`f2_*` は当期の中間予想。本決算は `fy_*` / `f2_*` を空にして来期予想を `raw_json` の `NxF*` / `NxF*2Q` / `NxtFYEn` に入れる。`NxtFYEn` は来期予想（配当を含む）の期末、無ければ年度末の 1 年後（J-Quants は来期予想が無くても入れる）。業績予想の修正は、中間予想があり配当予想が無ければ `type_of_period='2Q'`（期末は中間期末）、それ以外は `FY`（期末は年度末）。J-Quants と 2026-08 以降の 237 件中 217 件で同じ。残りは J-Quants の決め方が読めない
- `raw_json` は J-Quants のものがあれば残す。無ければ画面が読むキー（`F*` / `NxF*` / 配当 `Div*` `FDiv*` `NxFDiv*` / `BPS` / `EqAR` / `ROE`）だけ作り、`"Source": "xbrl"` を付ける。配当は単体の context にあるので連結の書類でも単体を見る
- `type_of_document` は J-Quants の形（`2QFinancialStatements_Consolidated_IFRS`、`EarnForecastRevision` など）
- 単体だけの業績予想の修正（個別業績予想の修正）は、J-Quants と同じく `raw_json` の `FNC*` に入れ、`fy_*` / `f2_*` は空にする（連結の会社の個別予想を連結予想と混ぜない）。ただし直近の決算短信が単体の会社は `fy_*` / `f2_*` に入れる。J-Quants はこの場合も `FNC*` だけなので、単体の会社の修正は J-Quants の行と違って上方 / 下方が出る
- REIT（題名に REIT）は作らず J-Quants の行のまま。決算速報の対象外で、1 口あたりの要素が `tse-re-t` で違う
- J-Quants が「決算速報値のお知らせ」を業績予想の修正として持つ行（KAB-222）は、XBRL では四半期の書類で実績が無いので作らない
- 突合は `tools/xbrl/xbrl_statements_check.py --db … --since …`（読み取り専用。作る行と J-Quants の行を列ごとに比べる）

### 3.3 コード規則

既存どおり:

- 保存・結合キーは 5桁 `^[0-9][0-9A-Z]{3}[0-9]$`
- 保存ディレクトリと manifest の `code4` は 4桁のまま
- 表示の 4桁と混同しない

---

## 4. データベース設計

### 4.1 ER

ER 図は [`schema/xbrl.md`](../schema/xbrl.md)（`app/models.py` から自動生成）。企業キーは `stocks.code` との文字列照合（§3.1）。

FK は XBRL テーブル同士にだけ付ける。`stocks` への FK は付けない（月次と同じ。`PRAGMA foreign_keys` も有効化しない）。parse 前に `db.get(Stock, code)` で存在確認する。

### 4.2 テーブル定義

SQLite 前提。Postgres に切っても動く型にする。`JSONB` / `GENERATED ALWAYS` / 部分 INDEX の `WHERE` は使わない。

テーブル・列・索引は [`schema/xbrl.md`](../schema/xbrl.md)（`app/models.py` から自動生成。列の意味もそこに出る）。

`unmapped` は VIEW ではなく `jobs/xbrl/xbrl_parse.py report-unmapped` のクエリで出す。VIEW は §18 の `xbrl_statement_values` だけ（定義は `app/xbrl/statement_values.py`、作るのは `init_db`）。

### 4.3 設計意図

- **縦持ち**: TDnet タクソノミも年度改訂される。列追加では持たない。
- **正規化層は再生成可能**: マッピング修正 → `--rebuild` で `xbrl_financials` を作り直す。手で INSERT しない。
- **マッピングは element_id ではなく `namespace_key + local_name`**: 年度で `namespace_uri` が変わる。element 行が増えても YAML を書き換えなくてよい。
- **金額は float にしない**: 既存の `statements` は Float だが、生 fact は `NUMERIC`（Decimal）。正規化層も同様。
- **容量**: 全 fact を数年分入れると行数が大きくなり得る。第1弾は同一 DB。膨らんだら `xbrl_*.db` への分離を検討（今は作らない）。
- **決算一覧の売上総利益**: `idx_xbrl_financials_gp_cover`（code, std_item_id, value_kind, … value, filing_id）と `idx_xbrl_filings_id_cover` で表本体を読まない（KAB-295）。旧 `idx_xbrl_financials_lookup` は先頭 2 列が同じなので `ensure_indexes` が外す。統計が無いと planner が parse_status 経由になるので、索引を足した表は ANALYZE する。
- **source**: `tdnet`（短信 ZIP。`doc_id` は ZIP 名）、`edinet`（有報・半期報）、`tdnet_pdf`（ZIP の無い短信 PDF。`doc_id` は開示番号 14 桁。`raw_path` は `tdnet_pdf:<開示番号>`。KAB-173、§16.5）。同じ 14 桁の `tdnet` が `parse_status='parsed'` になったら `tdnet_pdf` の filing は消す。`failed` / `no-ixbrl` の ZIP は消さない。

### 4.4 表示リンクの区分 `xbrl_presentation_roles`

書類の ZIP の表示リンク（`link:presentationLink`）は、どの要素がどの表に属し、どの順に印字されるかを会社が宣言したもの。読むのは `app/xbrl/presentation.py`（`read_presentation` が展開済みのフォルダの全区分を返し、`statement_roles` が財務3表に当たるものだけ残す）。取り込み（`parse_package` → `store_package`）で `edinet` / `tdnet` の書類ごとに入れ替える。`tdnet_pdf` は表示リンクが無いので行を持たない。

- **区分名 → 表**: 区分名（role URI の最後）が `rol_` で始まり、注記（`rol_Notes…`）・株主資本等変動計算書（`…ChangesInEquity…` / `…ChangesInNetAssets…` / `…UnitholdersEquity…`）でないもの。CF は `StatementOfCashFlows`、BS は `BalanceSheet` / `StatementOfFinancialPosition`、PL は `StatementOfIncome` / `StatementOfProfitOrLoss` / `StatementOfComprehensiveIncome` を含むもの。包括利益計算書も PL。連結は名前に `Consolidated` があるもの、IFRS は `IFRS` があるもの。前後の `Quarterly` / `SemiAnnual` / `Type1SemiAnnual` / `YearToQuarterEnd` / `Condensed` / `TDnet` / `-indirect` は見ない。1 つの書類に日本基準と IFRS の 2 組の表がある会社（どちらも個別）は `is_ifrs` で分かれる。分け方を直せるように生の区分名を持つ
- **要素の並び**: `presentationArc` の親子を `order` の順に深さ優先で辿った順（`loc` の出現順ではない）。親子は `loc` のラベルではなく要素で結ぶ（同じ要素に `loc` が 2 つあり、子として付く方と親になる方が別のラベルの書類がある）。根が複数なら `loc` の出現順。同じ要素が 2 回出る（CF の期首・期末の現金及び現金同等物）ときは 2 つとも残し、`preferredLabel` の種類（role の最後。`periodStartLabel` / `totalLabel` など）で見分ける。名前が `Heading` / `Table` / `Axis` / `Member` / `LineItems` で終わる要素は落とす。`…Abstract`（流動資産などの見出し）は残す。`use="prohibited"` の arc は同じ arc を消す
- **要素の深さ**: 要素ごとに、区分の根からの段（0 から）を `items_json` の 5 つ目に持つ。段は残した祖先の数で、落とした要素（`Heading` / `Table` / `LineItems` など）は数えない（落とした要素の子は、落とした要素の段に上がる）。数えると、表の中の `…Table` の子が `…Table` の兄弟より深くなり、並びだけからは親を取り違える。複数の pre.xml の同じ区分をつなぐときも、段はそれぞれの根から数える。§17 の見出しを宣言どおりに出すために持つ
- **見出しの表示名**: 見出しの要素（名前に `Abstract` を含む。fact を持たないので `xbrl_elements` に名前が無い）には、表示名を `items_json` の 6 つ目に持つ（ほかの要素は null。行名は fact の要素のラベルで足りる）。arc の `preferredLabel` の役割（role の URI のまま。`…/jppfs/Consolidated/role/label` の「その他の包括利益累計額」など、最後の段だけでは見分けられない）のラベル、無ければ標準の役割（`http://www.xbrl.org/2003/role/label`）のラベル。日本語だけ。引く順は、会社のラベルリンク（書類の中の xsd が `labelLinkbaseRef` で指す手元の `*lab.xml`）、次にタクソノミのラベルリンク（同じ xsd が指す `http…` のファイルを `XBRL_CACHE_DIR` の下で読む。取り込みは `parse_package` の `cache_dir`）。英語のラベルリンク（`-en.xml`）は読まない。見つからなければ null（§17 は要素名を出す）。`xbrl_elements` に入れない理由: 同じ要素でも書類と表ごとに `preferredLabel` で名前が変わり（連結と個別）、会社のラベルは書類ごとに違う。表示リンクの行と一緒に入れ替わる方が、取り込みのやり直しと合う。タクソノミのラベルリンクはプロセスの中で 1 回だけ読む
- 深さと表示名を持つ前に保存した行は要素が 4 つで、§17 は見出しを 大区分・区分 の 2 段で出す。深さだけで表示名の無い行（5 つ）は、§17 は見出しの要素名を出す。読み直しは §4.5 の `--redo-flat`
- **要素の名前空間**: `loc` の `xlink:href` の `#` の後ろ（xsd の要素 id）から引く。書類の中の xsd を指すときはその xsd の `name` 属性を local_name にし、id の残りを `namespace_key` にする（`jpcrp030000-asr_E…` / `tse-…-証券コード`）。標準タクソノミ（`app/xbrl/parser.py` の `STANDARD_NS_URI_ROOTS` の配下の URL）はファイル名から日付を落としたもの（`jppfs_cor`）を `namespace_key` にする。`is_custom` は標準タクソノミの URL でないもの。§17 の行のキー（標準は `local_name`、会社独自は `ext:<local_name>`）と突き合わせられる
- 同じ区分が複数の pre.xml にあれば、最初の区分の後ろに足す。`seq` は書類の中で保存した区分の順
- **済みの印 `xbrl_filings.presentation_status`**: NULL はまだ読んでいない。`ok` は 3 表の区分を 1 つ以上保存した、`none` は読んだが 3 表の区分が無い（業績予想の修正のお知らせ、CF の区分の無い有報・Q1/Q3 の四半期報告書と短信など。pre.xml が無い ZIP もここ）、`gone` は ZIP が手に入らない、`failed` は ZIP や表示リンクが読めない。取り込み（`store_package`）が毎回付け直す。`delete_filing_payload` は行を消して NULL に戻す。`tdnet_pdf` の filing は表示リンクが無いので `none`。行が無いだけでは「まだ」と「区分が無い」を見分けられないので列で持つ
- 既存の DB には `jobs/migrate.py` が列を足す（`ALTER TABLE … ADD COLUMN`。NULL の列なので表は書き換えない）。足したあとは全件 NULL なので、§4.5 のバッチで過去分を埋める

### 4.5 過去の書類の表示リンクを埋める（`jobs/xbrl/xbrl_presentation.py`）

`presentation_status` が NULL の `edinet` / `tdnet` の `parsed` の filing を、提出の新しい順に 1 件ずつ読む（`app/xbrl/presentation_backfill.py`）。ZIP を取る → `*pre.xml` と `*.xsd` と `*lab.xml` だけ展開（`AuditDoc` の下は展開しない）→ `read_presentation` → `store_presentation` と印 → commit。fact・taxonomy には触らない（`parse_package` を呼ばない）。止めても続きから進む。書き込みは `run_sqlite_write`（`database is locked` は待ってやり直す。やり直しても取れなければその書類は印を付けずに `errors`）。

- **ZIP の取り方**: 短信 XBRL は `--dest`（既定 `XBRL_DEST`）からの `raw_path`。無ければ `gone`。読む前に共有データのマウントを確かめる（`fail_if_mount_down`。切れていたら何もしない）。EDINET は `edinet_documents` の行を引いて `app/edinet/archive.py download`。手元（`data/edinet`）に無く、退避の記録（`edinet_archives` の `saved`）があれば EDINET には行かず OneDrive から読む（退避済みの ZIP を取り直して手元に書き直さない）。記録が無ければ EDINET（404 なら `gone`。閲覧期間を過ぎた書類は EDINET を飛ばす）。`edinet_documents` に行が無い・404 で退避も無いものは `gone`。5xx・タイムアウト・OneDrive の失敗は印を付けず `errors`（次の回にまた取る）。EDINET のメンテはその回を打ち切って exit 0（ほかの EDINET のジョブと同じ）。`EDINET_API_KEY` が無いとき・`--local-only` のときは手元（`data/edinet`）だけを見て、無い書類は印を付けずに飛ばす（`skipped`）
- **手元にたまる ZIP**: EDINET から取った ZIP は既存の取得と同じく `data/edinet` に書いて `edinet_archives` に記録する。`edinet_archives` に記録の無い書類をまとめて流すとその分が手元にたまり、翌朝の `jobs/ops/backup_data.py` が OneDrive へ移すまで残る。`--limit` で 1 日に流す件数を区切る
- **間隔**: `--sleep` 秒を、EDINET か OneDrive から取った書類のあとにだけ待つ（手元の ZIP では待たない）。EDINET のクライアントにも最小間隔がある
- 出力は `--batch N` で N 件ごとに 1 行、最後に `xbrl_presentation done= ok= none= gone= failed= errors= skipped= remaining=`。`errors` があれば exit 1。どちらの行にも、ZIP の出どころごとの件数と取得にかかった秒（`local=件/秒 edinet=件/秒 offsite=件/秒`。`offsite` が OneDrive）が付く
- `--archived-only` は退避の記録がある EDINET の書類だけを対象にする（OneDrive からの読み出しの時間を少ない件数で測るとき）
- `--offsite-chunk N` は、N 件ごとに、退避の記録があって手元に無い ZIP を OneDrive から一時フォルダへまとめて写し（`rclone copy --files-from --no-traverse`）、読んだら一時フォルダを消す。1 件ずつの `rclone cat` は 1 件ごとに置き場所をたどるので遅い。写せなかった ZIP は 1 件ずつの読み出しに戻る。写した秒は `offsite` の秒に入る
- cron には載せない。過去分を 1 回流すだけで、新しく取り込む書類は取り込みが印を付ける。`gone` / `failed` を流し直すときは、その filing の `presentation_status` を NULL に戻してから流す
- `--redo-flat` は、`presentation_status` が `ok` で要素に深さか見出しの表示名の無い書類（§4.4）を読み直す（NULL の書類は対象にしない。既定の動きは変えない）。有無は、書類の `seq` 0 の区分の `items_json` の最初の要素の長さ（`json_array_length(items_json, '$[0]') < 6`）で見る。区分は `store_presentation` が書類ごとに一度に書くので、1 つ見れば足りる。列は足さない（1 回きりの読み直しのために、全件に値を入れるマイグレーションと書き込みの手間をかけない。対象を選ぶときに `seq` 0 の行の JSON を読むだけ）。読み直しで ZIP が無い・読めない・3 表の区分が無いときは、今の行と印を残し `gone` / `failed` / `none` と数えるだけ（深さの無い行でも並びの決まりで区分は出るので、消すより残す）。そのため `remaining` は 0 にならないことがある

---

## 5. パーサ（xbrlp）

### 5.1 採用理由

Arelle は 1 ファイル数秒。xbrlp は標準ライブラリのみで iXBRL の fact / ラベル / 表示・計算リンクを読む。著者計測でおおよそ 20 倍。依存を増やしたくない stock-analyzer の方針にも合う。

ソース: https://gist.github.com/kawasin73/346ed6a24382a2b238e1ff0d10f3f40c  
ファイル: `__init__.py` / `file_loader.py` / `parser.py`  
リビジョンは vendoring 時の gist commit をコメントで固定する。

### 5.2 置き場所

gist は pip パッケージではない。`app/xbrlp/` にソースをコピーする（外部 git submodule は使わない）。

触るのはラッパだけ。gist 本体は改変しない。例外: `app/xbrlp/file_loader.py` は KAB-60 でダウンロードを廃止し、同梱ディレクトリだけを読む。

```
app/xbrlp/          # vendored。改変しない
app/xbrl/
  __init__.py
  parser.py         # ZIP展開・iXBRL特定・xbrlp呼び出し・context 解釈
  mappings.py       # YAML → DB
  normalize.py
config/xbrl/
  std_items.yaml
  mappings/
    pl_jpgaap.yaml
    bs_jpgaap.yaml
    cf_jpgaap.yaml
    ifrs.yaml
jobs/xbrl/xbrl_parse.py
jobs/xbrl/xbrl_normalize.py
tests/xbrl/test_xbrl_parse.py
```

### 5.3 xbrlp が提供するもの / しないもの

提供する:

```python
from app.xbrlp import Parser, FileLoader, Fact, QName, Label, Arc

parser = Parser(file_loader=FileLoader(cache_dir=cache_dir), shared_xsd_cache=shared)
parser.prepare_ixbrl(manifest_xml_path)
for fact in parser.load_facts():
    fact.qname.prefix          # namespace_key 相当
    fact.qname.local_name
    fact.qname.namespace_uri
    fact.context_ref           # 文字列
    fact.unit_ref
    fact.is_numeric
    fact.is_nil
    fact.scale, fact.sign, fact.format
    fact.value                 # Decimal（数値）または str。scale/sign 適用済
for lab in parser.load_labels():
    ...
```

提供しない（ラッパが担う）:

- `xbrli:context` の period / entity / scenario / segment
- unit の実体（`unitRef` → JPY 等への解決）
- 伝統的な instance `.xbrl`（`ix:nonFraction` が無い文書）
- AuditDoc

コンテキストは同一 iXBRL を `xml.etree` で再走査して取る。xbrlp の `Fact.raw_element` に依存して context を復元しない（要素は iterparse 後に空になる）。

### 5.4 TDnet ZIP 内の対象ファイル

展開後、次の順で iXBRL を探す。

1. ZIP 内の `Summary/` を先頭に、続く `*.htm` / `*.html` / `*.ixbrl` で `ix:nonFraction` または `ix:nonNumeric` を含むもの（`find_ixbrl_files`）
2. `*manifest*.xml` があり `<ixbrl>` を列挙していればそれを足す（xbrlp `prepare_ixbrl`）。Attachment の manifest だけあっても Summary 側は捨てない（KAB-48）
3. `AuditDoc/` / 監査関連パスは除外
4. 対象 iXBRL が 0 件なら `parse_status='skipped'`、`parse_error='no-ixbrl'`

第1弾は伝統的 instance `.xbrl` だけしか無い ZIP をパースしない（skipped）。有報を始めるときに拡張する。

タクソノミはリポジトリ同梱の `XBRL_CACHE_DIR`（既定 `data/xbrl_taxonomy`）だけを読む。`urlopen` しない。参照ファイルが無い filing は `parse_status='failed'`、ログに欠けた URL/パスを出す。復旧は同梱を足して `xbrl_parse.py --retry-failed`。版は filing の `schemaRef` 等が指すもの（過去年を含む）をすべて残す。走査・同期は `tools/xbrl/taxonomy_bundle.py`（`check` / `sync` はパーサと同じ解決：`schemaRef` → `xsd:import` と `load_labels` のラベル linkbase。汎用ラベル・定義リンク・コメント上の URL は数えない）。プロセス内では remote の XSD とラベル linkbase を使い回す（`parse_package` の shared cache）。ラベル linkbase は href ごとに (prefix, local) → 最初の ja / en に縮約して持ち、filing ではファクトに出る要素だけ引く（KAB-63）。DB へは要素を filing 単位でまとめて SELECT / INSERT し、変わった行だけ UPDATE、コンテキストとファクトは bulk insert する。

`xbrl_parse.py` は ZIP の展開とパース（DB に触らない `parse_zip`）を `XBRL_PARSE_WORKERS` 個のプロセスで先読みし、DB 書き込みは親 1 本が manifest 順に行う（KAB-63。1 で直列）。順番を守るので、直近 filing からの会計基準引き継ぎは直列と同じになる。各行の skip 判定は書き込みの順番が来たときにもう一度行う。ワーカーの例外は親で直列と同じく `failed` に記録し、ワーカーが落ちたらその行は親でパースし直す。既定 2。DB 書き込みが 1 件約 30ms で律速するので、3 以上にしてもほぼ速くならない。

コンテキストは iXBRL から `xbrli:context` のブロックだけを切り出してパースする。コメント / CDATA / ENTITY がある、開始タグとブロックの数が合わない、パースに失敗したファイルは従来どおり全体を iterparse する。開始タグが無いファイル（Attachment 本表は `contextRef` だけ）はコンテキスト 0 件。

### 5.5 コンテキスト解釈

`xbrli:context` について:

**period**

- `instant` → `period_type='instant'`、`end_date=instant`、`start_date=NULL`
- `startDate/endDate` → `duration`

**dimensions**

- `scenario` / `segment` の `explicitMember` を dict にし JSON 文字列で保存
- キー: `prefix:axisLocalName`、値: `prefix:memberLocalName`
- dict が空でなければ `has_dimensions=1`

**is_consolidated**

1. メンバーに `ConsolidatedMember` を含む → 1
2. `NonConsolidatedMember` を含む → 0
3. どちらも無い、または判定不能 → **1（連結）**。短信は連結がデフォルト。単体と断定できるときだけ 0。
4. 正規化は `is_consolidated` が 0/1 どちらでも対象にする（NULL は残さない）

**relative_period**

- filing の当期期末（優先順: DEI の CurrentPeriod/DocumentPeriod → 年次タイトルの `YYYY年M月期` → 本表 PL の duration 期末 → 本表も DEI も無ければ短信サマリーの `ResultMember` duration 期末 → 全 duration の最新 `end_date`）と一致 → `current`
- ほぼ 1 年前（±30 日）→ `prior1`
- ほぼ 2 年前 → `prior2`
- それ以外 → NULL
- 本表または DEI がある書類ではサマリー実績を期末判定に使わない（KAB-59）

**doc_type / period_type / is_amendment**

manifest の `title` から機械判定する。取りこぼしは `other` / NULL。第1弾でタイトル規則をコードにベタ書きしてよいが、後で YAML に出す。

目安（確定ではない。fixture で検証）:

- 「訂正」を含む → `is_amendment=1`
- 「四半期」→ `doc_type=quarterly`。第1/2/3 四半期の語があれば `Q1/Q2/Q3`
- 「半期」「中間」→ `semiannual` / `Q2`
- 「通期」「決算短信」で四半期でない → `annual` / `FY`

会計基準はタイトルの IFRS / SFRS / 国際会計 → `IFRS`、米国 → `USGAAP`、日本基準 → `JPGAAP`。タイトルが空なら DEI または名前空間に `jpigp` / `ifrs`、tse-ed-t 等の数値 `*IFRS` fact があれば `IFRS`、`us-gaap` なら `USGAAP`。それでも空なら同じ銘柄の直近 parsed filing から引き継ぐ。曖昧なら NULL（JPGAAP と決めない）。

---

## 6. 処理仕様

### 6.1 fetch（既存）

仕様の正本は `jobs/xbrl/tdnet_xbrl_r2.py` の docstring。本設計では変えない。

- 入力: 日数 N（cron は 2。前日と当日。前日 23 時より後の開示を翌日に拾う）
- 出力: `{dest}/{code4}/YYYYMMDD_..._{xbrl_id}.zip` と `manifest/{YYYY-MM-DD}.jsonl`
- skip 正本は lookback 内 manifest の `xbrl_id`
- マスタ無し・インフラは `_unmatched/` に保存（消さない）

parse ジョブは manifest を入力カタログにする。fetch 側に parse_status を足さない。

### 6.2 parse

入力: dest 配下の manifest（期間指定）または `--doc-id`

1. manifest 行を読む。`path` の ZIP が無ければ error_count++ して次へ。
2. `jq_lookup_code(code or code4)` が `stocks` に無ければ skip（`_unmatched` は対象外）。
3. 既に `xbrl_filings` が `(tdnet, doc_id)` で `parse_status='parsed'` かつ `raw_sha256` が同じなら skip。
4. ZIP を一時ディレクトリに展開（`data/` 上には展開しない）。
5. §5.4 で iXBRL を特定し xbrlp で fact / label を読む。
6. 同じファイル群から context を読む。
7. elements UPSERT（`namespace_uri + local_name`）。`namespace_key` は prefix。標準タクソノミ以外は `is_custom=1`。標準は名前空間 URI が `disclosure.edinet-fsa.go.jp/taxonomy/` か `www.xbrl.tdnet.info/taxonomy/` の配下のもの。URI が無いときだけ `namespace_key` の完全一致（`jppfs_cor` / `jpcrp_cor` / `tse-ed-t` など。`app/xbrl/parser.py STANDARD_NS_KEYS`）。部分一致にしない（EDINET の会社独自 `jpcrp030000-asr_E…` はキーにも URI にも jpcrp が入る。KAB-81）。既存行は `jobs/migrate.py` が付け直す。リストを勝手に増やさない。
8. facts INSERT。数値は `Fact.value`（Decimal, scale/sign 済）を `value_num` へ。非数値は 1000 文字以内だけ `value_text`。超過は fact 行自体を作らない（要素は登録済み）。`xsi:nil` は `is_nil=1`、値 NULL。
9. 表示リンク（`*pre.xml`。`AuditDoc` の下は読まない）の区分のうち財務3表に当たるものを `xbrl_presentation_roles` に入れる（§4.4）。読めない・無いときはその書類の行が 0 行になるだけで、取り込みは止めない。
10. 成功なら `parse_status='parsed'`。例外は filing 単位でロールバックし `failed` + 要約トレース。ジョブは止めない。
11. 再パースは当該 `filing_id` の contexts と表示リンクの行を DELETE（facts も CASCADE 相当をアプリ側で消す。SQLite で FK が効かないため明示 DELETE）。

レート制限は TDnet に触れないので不要。同梱に無いタクソノミ URL は filing を `failed` にする（ネットワークには出ない）。`xbrl_parse.py` の `errors=` と cron の非ゼロ終了が既存の Slack バッチアラートに乗る。

### 6.3 load-mappings

`config/xbrl/` の YAML が正本。DB を直接編集しない。

```
python jobs/xbrl/xbrl_parse.py load-mappings
```

差分 UPSERT。YAML から消えたマッピングは削除。`xbrl_std_items` も同様。

YAML の `ns` は `namespace_key`。URI 完全一致では解決しない。

### 6.4 normalize

filing 単位。

1. セグメント等、連結/実績予想/レンジ/配当以外の次元が付いた fact は捨てる（セグメントは §14 の `xbrl_segments` へ。`statement='SEG'` の科目は `xbrl_financials` に使わない）
2. **既存23項目（BS/PL/CF）の実績**: 従来どおり `has_dimensions` が空か連結/非連結だけ、かつ `relative_period='current'`。選定キーは `std_item` ごと 1 値（値を変えない）
2b. **売上・営業利益・経常利益・純利益の実績はサマリーを先に（KAB-375）**: 短信 ZIP のサマリー（`ZIP_SUMMARY_NAMESPACES` = `tse-ed-t` と REIT の `tse-re-t`）に当期実績（`ResultMember`、点）があれば、2 の本表の値より優先する。J-Quants（`statements`）はサマリーの値を持つので、決算速報を XBRL に寄せるときに揃う。本表を選ぶと、小売の営業収益（サマリー・J-Quants）と売上高（本表）、IFRS の 3か月と累計の取り違え、本表に営業利益の無い IFRS 会社でずれていた。サマリーに無い科目は 2 の本表のまま。連結を先に、同じ連結区分なら mapping の `priority`。会社独自の拡張タグはマッピングしない。出どころは `source_layer`（`primary` / `summary`）。EDINET はサマリーが無いので変わらない
2c. **PDF の実績売上のサマリー補完（KAB-58 / KAB-59）**: 短信 PDF（`pdf-sum`）は、本表経路で `net_sales` 実績が 1 件も取れなかったときだけサマリーの当期実績を使う。本表で取れている値は上書きしない（PDF サマリーの読み取りは凍結。§16.5）
3. **EPS・BPS・自己資本比率・ROE の実績**（`SUMMARY_ACTUAL_CODES`）: 本表コンテキストに加え `ResultMember`（短信表紙）を許す。レガシー 23 科目の実績は 2b・2c のほかは表紙の `ResultMember` を受けない。前期の実績 context は `is_normalizable_context` が落とす（PDF の fact を入れるときも同じ）
4. **配当**: `FirstQuarter` / `SecondQuarter` / `ThirdQuarter` / `YearEnd` / `Annual` メンバーで `target_period` を分ける。`ResultMember` は実績、`ForecastMember` は予想
4b. **有報の科目（KAB-78）**: 上の 2〜4 に当たらない科目（§7.2 の有報の科目）は、当期（`relative_period='current'`）の実績の点で、連結/単体以外の次元が無い fact。連結と単体は別の行（`is_consolidated`）。セグメント別・所有者別（`Row1Member` 等）・前期・提出日現在は入らない。mapping の `part: months` は同じ科目・context の値に 月÷12 を足す（小数 2 桁）。DEI `WhetherConsolidatedFinancialStatementsArePreparedDEI` が false の書類（連結子会社が無い会社）は、連結の軸が無い context（設備の状況・研究開発活動・自己株式等など）も単体にする（全科目。TDnet は DEI が無いので今までどおり）
5. **業績予想**: `ForecastMember`、または `LowerMember`/`UpperMember`/`MinimumMember`/`MaximumMember`（ForecastMember 無しのレンジ）。`relative_period='current'` は要求しない（通期予想の期末は短信の四半期末とずれる）。`PreviousMember` は `value_kind=forecast_previous`（今回予想は `forecast`）。`LowerMember`/`UpperMember` は `range_side`。context 名の `accumulatedq1`/`accumulatedq3` と日数（80–100→Q1、240–300→Q3）で `target_period` を付ける
6. `is_consolidated` が取れなければ 1（連結）として正規化する。NULL のまま残さない
7. mappings を `namespace_key + local_name` で結合。`accounting_standard` が NULL でない行は filing の基準と一致するときだけ。例外: 要素の `local_name` が `*IFRS` なら、filing が JPGAAP / NULL でも IFRS マッピングを使う（40530 の予想）。同一キーでは `priority` 昇順で最初の値。**IFRS の書類では `jppfs_*` 要素を正規化に使わない**（KAB-52 案B。IFRS 側に対応が無い科目は空欄）
8. `sign='invert'` なら符号反転。`xbrl_financials.unit` は fact の単位。fact が `pure`（または空）のときだけ科目の `unit`（人数・年数・比率を `persons` / `years` / `ratio` にする。KAB-147）。どちらも無ければ `JPY`
9. filing 単位で既存の `xbrl_financials` を消してから入れ直す
10. `is_required=1` が欠けたら警告ログ（`code, filing_id, std_item.code`）。処理は継続。required は本表実績だけ

通常実行は `xbrl_financials` が無い filing と、`parsed_at` が既存の `generated_at` より新しい filing だけ。`--rebuild` は `xbrl_financials` と `xbrl_segments` と大株主の表を全削除して全 parsed filing を再処理する。ログの `deleted` / `segments_deleted` は消す前、`financials` / `segments` は作り直した後の行数（KAB-83。正規化が変わっていなければ前後が同じ）。`--code` で銘柄を限定できる。大株主は軸付き fact が無ければ 0 行のまま（`docs/jobs/edinet.md`「有報の大株主の状況・所有者別状況」）。

### 6.5 CLI

```bash
# 既存
./run.sh 相当で python jobs/xbrl/tdnet_xbrl_r2.py 1

# 新規
python jobs/xbrl/xbrl_parse.py                     # pending + 未登録 ZIP
python jobs/xbrl/xbrl_parse.py --from 2026-01-01 --to 2026-09-05
python jobs/xbrl/xbrl_parse.py --doc-id 2026090750xxxx
python jobs/xbrl/xbrl_parse.py --retry-failed
python jobs/xbrl/xbrl_parse.py --reparse   # 既に parsed の ZIP も再読込（KAB-48）
python jobs/xbrl/xbrl_parse.py load-mappings
python jobs/xbrl/xbrl_parse.py report-unmapped --limit 50

python jobs/xbrl/xbrl_normalize.py
python jobs/xbrl/xbrl_normalize.py --rebuild
python jobs/xbrl/xbrl_normalize.py --rebuild --code 72030
python jobs/xbrl/xbrl_normalize.py --since 2026-08-01 --source tdnet
python jobs/xbrl/xbrl_statements.py
python jobs/xbrl/xbrl_statements.py --since 2026-08-01
```

dest 既定は `tdnet_xbrl_r2.DEFAULT_DEST`。`--dest` で上書き。

cron は 12–23 時 JST の毎時 :00 に fetch → parse → normalize → statements（§3.2。直近 3 日の短信）を 1 行で順に走らせる（段の間は `;`。fetch が失敗しても parse / normalize / statements は走らせる。手元の未解析 ZIP を拾う）。:10 / :20 の単独 cron は置かない（次の毎時の連鎖が拾う。手でやり直すときは各ジョブを `deploy/cron-run.sh` で単独実行）。短信 PDF の数値（§16.5）は cron に無い。normalize は未反映と再パース分だけ（マッピング変更は `--rebuild`）。`--rebuild` は最初に全部消すので、作り直しの間は修正履歴・粗利などが空になる。範囲を絞れるときは `--since YYYY-MM-DD`（`--source` で tdnet / tdnet_pdf / edinet に限定）を使う。提出日がその日以降の parsed filing を、消さずに 1 件ずつやり直す。`--rebuild` とは併用しない。

---

## 7. 標準勘定科目とマッピング

### 7.1 管理

YAML のみ。コードに科目名をハードコードしない。

```yaml
# config/xbrl/mappings/pl_jpgaap.yaml
- std_item: net_sales
  elements:
    - { ns: jppfs_cor, name: NetSales,          priority: 10 }
    - { ns: jppfs_cor, name: OperatingRevenue1, priority: 20 }
```

TDnet 短信の実際の `namespace_key` は fixture を見てから YAML に書く。`tse-ed-t` 等を実装前に決め打ちしない。

### 7.2 初期定義（最小セット）

第1弾の BS/PL/CF 23項目に加え、KAB-47 で EPS・配当・業績予想、KAB-78 で有報の科目を足した。これ以外の科目は確認なく足さない。

**BS（instant）**

| code | label_ja | 代表 local_name |
|---|---|---|
| cash_and_deposits | 現金及び預金 | CashAndDeposits |
| current_assets | 流動資産合計 | CurrentAssets |
| noncurrent_assets | 固定資産合計 | NoncurrentAssets |
| total_assets | 資産合計 | Assets |
| current_liabilities | 流動負債合計 | CurrentLiabilities |
| noncurrent_liabilities | 固定負債合計 | NoncurrentLiabilities |
| total_liabilities | 負債合計 | Liabilities |
| shareholders_equity | 株主資本合計 | ShareholdersEquity |
| net_assets | 純資産合計 | NetAssets |

`interest_bearing_debt` は合成前提なので初期 YAML に含めない。

**PL（duration）**

| code | label_ja | 代表 local_name |
|---|---|---|
| net_sales | 売上高 | NetSales / OperatingRevenue1 / GrossOperatingRevenue / OrdinaryIncomeBNK / OperatingRevenue2 / Revenue / OperatingRevenueRWY / ELE / SEC / NetSalesOfCompletedConstructionContractsCNS / RevenueIFRS / NetSalesIFRS / Revenue2IFRS。本表欠落時は tse-ed-t の NetSales / OperatingRevenues / GrossOperatingRevenues / Revenue / SalesIFRS 等。営業総収入は売上高（内訳）より優先 |
| cost_of_sales | 売上原価 | CostOfSales |
| gross_profit | 売上総利益 | GrossProfit |
| sga | 販売費及び一般管理費 | SellingGeneralAndAdministrativeExpenses |
| operating_income | 営業利益 | OperatingIncome |
| ordinary_income | 経常利益 | OrdinaryIncome |
| profit_before_tax | 税引前当期純利益 | IncomeBeforeIncomeTaxes |
| net_income | 親会社株主に帰属する当期純利益 | ProfitLossAttributableToOwnersOfParent / ProfitLossIFRS（単体 IFRS） |
| eps_basic | 1株当たり当期純利益 | tse-ed-t:NetIncomePerShare / BasicEarningsPerShare |
| eps_diluted | 潜在株式調整後1株当たり当期純利益 | DilutedNetIncomePerShare / DilutedEarningsLossPerShareIFRS |

日本基準の有報は本表（PL）に EPS の数値 fact が無い。KAB-78 で主要な経営指標等の `jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults` / `DilutedEarningsPerShareSummaryOfBusinessResults`（priority 50）と、その IFRS・米国基準版（`…IFRSSummaryOfBusinessResults` / `…USGAAPSummaryOfBusinessResults`。40）を足した。連結・単体の両方が入る

**配当（OTHER。実績/予想は `value_kind`、期は `target_period`）**

| code | label_ja | 代表 local_name |
|---|---|---|
| dps | 1株当たり配当金 | tse-ed-t:DividendPerShare |

業績予想は新しい std_item を増やさず、`net_sales` / `operating_income` / `ordinary_income` / `net_income` / `eps_basic` / `dps` を `value_kind=forecast` で持つ。通期は `target_period=FY`、中間は `H1`。レンジは `range_side=lower|upper`。

**CF（duration）**

| code | label_ja | 代表 local_name |
|---|---|---|
| operating_cf | 営業活動によるCF | NetCashProvidedByUsedInOperatingActivities |
| investing_cf | 投資活動によるCF | NetCashProvidedByUsedInInvestmentActivities |
| financing_cf | 財務活動によるCF | NetCashProvidedByUsedInFinancingActivities |
| capex | 設備投資額 | PurchaseOfPropertyPlantAndEquipment（sign: invert） |
| depreciation | 減価償却費 | DepreciationAndAmortizationOpeCF |
| cash_end | 現金及び現金同等物期末残高 | CashAndCashEquivalents |

**有報の科目（OTHER。KAB-78。`config/xbrl/mappings/annual_report.yaml`）**

有報（EDINET）にだけある値。主要な経営指標等・従業員の状況・株式等の状況・設備の状況・研究開発活動から、当期の点を取る（§6.4-4b）。連結と単体は別の行。単位は fact の単位（USD 開示は `USD` / `USDPerShares`）。fact が `pure`（人数・年数・比率）のときだけ `std_items.yaml` の `unit`（`persons` / `years` / `ratio`）。

| code | label_ja | unit | 要素（`jpcrp_cor`。priority の小さい順） | 連結/単体 |
|---|---|---|---|---|
| bps | 1株当たり純資産 | JPYPerShares | `EquityToAssetRatioIFRSSummaryOfBusinessResults`（名前に反してラベルは「１株当たり親会社所有者帰属持分」）/ `EquityAttributableToOwnersOfParentPerShareUSGAAPSummaryOfBusinessResults` → `NetAssetsPerShareSummaryOfBusinessResults` | 両方 |
| equity_ratio | 自己資本比率 | ratio（0.471 = 47.1%） | `RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults` / `EquityToAssetRatioUSGAAPSummaryOfBusinessResults` → `EquityToAssetRatioSummaryOfBusinessResults` | 両方 |
| roe | 自己資本利益率 | ratio | `RateOfReturnOnEquityIFRSSummaryOfBusinessResults` / `…USGAAPSummaryOfBusinessResults` → `RateOfReturnOnEquitySummaryOfBusinessResults` | 両方 |
| shares_issued | 発行済株式総数 | shares | `TotalNumberOfIssuedSharesSummaryOfBusinessResults`（提出会社の経営指標等。期末） | 単体 |
| treasury_shares | 自己株式数 | shares | `TotalNumberOfSharesHeldTreasurySharesEtc`（自己株式等の表の計。所有者ごとの行は `Row*Member` で入らない） | 連結（context に軸が無い） |
| employees | 従業員数 | persons | `NumberOfEmployees`（セグメント別は入らない） | 両方 |
| employee_avg_age | 平均年齢 | years | `AverageAgeYears…` + `AverageAgeMonths…`（`part: months`。「43歳10ヶ月」→ 43.83） | 単体 |
| employee_avg_tenure | 平均勤続年数 | years | `AverageLengthOfServiceYears…` + `…Months…`（同上） | 単体 |
| employee_avg_salary | 平均年間給与 | JPY | `AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees` | 単体 |
| rd_expenses | 研究開発費 | JPY | `ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities`（研究開発活動の節）→ `jpigp_cor:ResearchAndDevelopmentExpenditureRecognizedAsExpenseDuringPeriodIFRS` → `ResearchAndDevelopmentExpensesIncludedInGeneralAndAdministrativeExpensesAndManufacturingCostForCurrentPeriod`（注記）→ `jppfs_cor:ResearchAndDevelopmentExpensesSGA` | 両方 |
| capex_reported | 設備投資額（設備の状況） | JPY | `CapitalExpendituresOverviewOfCapitalExpendituresEtc`（設備投資等の概要の合計。セグメント別だけの会社は空） | 連結 |

- `capex`（CF の有形固定資産の取得。符号反転）とは別の科目。設備の状況の設備投資額は無形資産を含み発生ベースで、CF と一致しない
- 研究開発活動の節は「221億円」のように丸めて書く会社がある（注記の 22,134 百万円と 1 億未満ずれる）。有報の記載どおりに入れる
- 取らないもの: 主要な経営指標等の前期以前（Prior1〜4。`keep_fact` で落ちる）、提出日現在の発行済株式数（`FilingDateInstant`。当期でない）、セグメント別の従業員数、PER・配当性向・株主総利回り
- 要素名と件数（2026-10-23）: 本番の EDINET 有報（日本基準 7,838 / IFRS 667 / 米国基準 12 件）で、上の要素はどれも当期・次元なしで 90% 以上の書類にある。ただし研究開発活動の節は日本基準 4,187 件（研究開発費が無い会社は書かない）、設備投資等の概要の合計は 7,150 件、平均年齢の月は 544 件（6%）
- 検証（2025-06 提出の有報 155 社。日本基準 118・IFRS 33・米国基準 4。`app/edinet/xbrl.py` と同じ経路で入れた `xbrl_financials`）
  - 有報 HTML の表示（`ix:nonFraction` の文字 × scale。xbrlp を通さない別実装）と、上の科目と EPS の 2,701 行がすべて一致
  - IR Bank `get_financials`（百万円）と: EPS 155/155 一致。自己資本比率は 153/155（ずれた 2 社は IR Bank の計算値。有報の記載とは一致）。研究開発費は 84 一致・29 ずれ（丸めた記載、IR Bank が販管費の研究開発費だけを採る会社）。設備投資額は 129 一致・12 ずれ（IFRS で IR Bank が CF の値を採る会社など）・4 社は当方空（セグメント別だけ）
  - BPS ×（発行済株式数 − 自己株式数）と 自己資本比率 × 総資産 が 136/142 社で 3% 以内（外れた 6 社は期末後の株式分割・優先株など。値は有報の記載どおり）
- 検証 2 回目（2025 年に提出された有報から、1 回目の会社を除いてランダムに 100 社。日本基準 95・IFRS 5。決算月は 3 月 55・12 月 12・9 月 7 ほか）
  - 有報 HTML の表示と 1,595 行すべて一致
  - IR Bank と（期が取れた 98 社）: EPS 97/98（ずれた 1 社は IR Bank の値が有報と違う）、自己資本比率 95/98（保険会社など IR Bank の計算値）、研究開発費 49 一致・3 ずれ（丸め・販管費だけ）、設備投資額 83/83
  - 単体だけの会社で、研究開発費・設備投資額・自己株式数が「連結」の行になっていたのを、この検証で見つけて直した（上の DEI の規則）
  - BPS の整合は 95/100 社で 3% 以内。外れた 5 社は優先株（BPS がマイナス・数円）や株式分割で、値は有報の記載どおり

代表 local_name は例であり網羅ではない。実装後 `report-unmapped` を `fact_count DESC` で出し、上位の標準タクソノミ要素を実装者へ報告する。**エージェントが独断でマッピングを足さない。**

TDnet 短信 ZIP には BS/CF が薄い（PL 中心のサマリ）ものが多い。欠損は正常。`is_required` は売上・営利・純利・資産合計など、短信に普通あるものだけ 1 にする。

### 7.3 IFRS・業種

- IFRS は `jpigp_cor` 等。`accounting_standard='IFRS'` のマッピングは filing の基準一致時のみ。ただし要素名が `*IFRS` なら filing の基準が JPGAAP / NULL でも使う（KAB-55 / 40530）
- IFRS の書類では `jppfs_*` を正規化に使わない。日本の制度上、単体決算書は日本基準のため IFRS 短信に両基準の本表が同居することがある（436A0）。IFRS 側に対応が無い科目は空欄
- IFRS に経常利益は無い。欠損はエラーにしない
- KAB-73 で IFRS 本表の要素を足した（承認済み）: 流動資産 `CurrentAssetsIFRS` / 非流動資産 `NonCurrentAssetsIFRS` / 流動負債 `TotalCurrentLiabilitiesIFRS` / 非流動負債 `NonCurrentLabilitiesIFRS`（タクソノミの綴り）/ 負債 `LiabilitiesIFRS` / `shareholders_equity` に親会社の所有者に帰属する持分 `EquityAttributableToOwnersOfParentIFRS`（日本基準の株主資本より広い）/ 売上原価・売上総利益・販管費 / 投資・財務 CF / 設備投資 `PurchaseOfPropertyPlantAndEquipmentInvCFIFRS` / 減価償却費 `DepreciationAndAmortizationOpeCFIFRS` / 現金同等物 `CashAndCashEquivalentsIFRS`。TDnet の IFRS 短信の添付本表にも効くので、入れたら `xbrl_normalize.py --rebuild`
- 銀行等の営業収益・建設完成工事高・鉄道/電力/証券の営業収益、営業総収入は priority フォールバックで吸収。業種分岐コードは書かない
- タイトルの `SFRS`（指定国際会計基準）は IFRS。タイトルが空のときは tse-ed-t の `*IFRS` 数値 fact、それが無ければ同じ銘柄の直近 parsed filing から `accounting_standard` を引き継ぐ。タイトルに日本基準がある書類は上書きしない

---

## 8. 設定

`app/config.py` に足す（ハードコードしない）。

```
# 未設定なら {repo}/data/xbrl。ルートだけ変えるときは SHARE_ROOT。
XBRL_DEST={repo}/data/xbrl
# SHARE_ROOT={repo}/data
XBRL_CACHE_DIR=data/xbrl_taxonomy
XBRL_TEXT_MAX_CHARS=1000
XBRL_PARSE_WORKERS=2
```

`.env.example` にコメントで追記する。必須キーは増やさない。

---

## 9. 非機能

| 項目 | 要件 |
|---|---|
| 冪等性 | 同じ ZIP/sha256 を再パースしても fact 件数が変わらない |
| トランザクション | filing 単位。部分 fact を残さない |
| ログ | 既存バッチと同様に stdout。`doc_id` と `code` を含める。JSON 必須にはしない |
| 依存 | requirements に Arelle / lxml / pyyaml 以外の大型パーサを足さない。YAML は PyYAML を足してよい |
| 実行環境 | VPS の `.venv`。ZIP は `data/xbrl`。タクソノミはリポジトリ同梱（`XBRL_CACHE_DIR`）。ローカルは `--dest` で ZIP ディレクトリを指す |

---

## 10. テスト

`tests/xbrl/test_xbrl_parse.py`。実 ZIP は `tests/fixtures/xbrl/` に最低 5 件。git に大きな ZIP を載せたくなければテストは「ファイルが無いとき skip」でもよいが、CI で 1 件は欲しい。

必ず含める種類:

1. 製造業・JGAAP・連結（短信）
2. 銀行（売上相当が OperatingRevenue 系）
3. IFRS
4. 単体のみ
5. カスタム要素が多いもの

検証:

- facts が 0 件でない（skipped 以外）
- BS が取れる fixture では `total_assets ≈ total_liabilities + net_assets`（±1）
- 同一 ZIP を 2 回パースしても fact 件数が同じ
- 正規化: 取れる fixture で `net_sales` / `operating_income` がハードコード期待値と一致
- YAML の `ns + name` が投入後の `xbrl_elements` に存在する（TDnet に無い ns はテストを分けて「未解決でも load-mappings は成功」）

ネットワーク無し。XSD は fixture 側にキャッシュを同梱するか、テスト用 FileLoader でローカルへ向ける。

---

## 11. 実装順

各ステップの終わりでコマンドが通ること。

1. `app/xbrlp/` に gist を vendor。1 ZIP を `load_facts` してコンソールに qname / value / context_ref を出せる
2. context ラッパ。同じ ZIP で instant/duration・dimensions を出せる
3. `models.py` に `xbrl_*` を追加。`init_db` / migrate でテーブルができる
4. `xbrl_filings` 登録 + parse → DB。1 銘柄で資産等式または売上非 NULL を確認
5. `config/xbrl/` の最小 YAML + `load-mappings`
6. normalize + `--rebuild`
7. `report-unmapped` を実行し上位要素を報告（マッピング追加は確認後）
8. テスト
9. cron は 12–23 時 JST の毎時 :00 に fetch → parse → normalize を続けて走らせる

フロント・`statements` 置換・EDINET はこのリストの外。

---

## 12. 禁止事項

- `companies` テーブルを新設して `stocks` と二重マスタにしない
- `stocks.edinet_code` を必須にしない
- 勘定科目を列にしたテーブルを作らない
- `xbrl_financials` に手で INSERT する処理を書かない
- マッピングをコードに埋め込まない
- `namespace_uri` 完全一致でマッピングしない
- パースできなかった要素を黙って捨てない（elements には残す。長文テキストの値だけ捨てる）
- §7.2 に無い科目のマッピングを確認なく足さない
- `statements` を XBRL 結果で上書きしない
- xbrlp 本体をローカル改変して context 対応をねじ込まない（ラッパ側）
- Arelle を dependencies に足さない

---

## 13. 後続（今書かない）

- （済）EDINET API v2 で有報・半期報 ZIP を取り、`source='edinet'` の filing を足す（KAB-73。`docs/jobs/edinet.md`「有報・半期報の財務 XBRL」）
- 伝統的 instance `.xbrl` パーサ
- 正規化層の実績を決算詳細 API に出す（`statements` の予想列は残す）
- facts の DB 分離

---

## 14. セグメント注記（KAB-77）

有報・半期報・四半期報のセグメント情報（報告セグメントごとの売上高・利益・資産）を `xbrl_segments` に正規化する。`xbrl_financials` には次元を足さない（§12「勘定科目を列にしたテーブルを作らない」を守るため縦持ちの別表。`xbrl_financials` の UNIQUE とクエリを変えない）。

### 14.1 実データで分かったこと（2025 年の有報 11 社・半期報 3・四半期報 3）

- 数値で取れる軸は `jpcrp_cor:OperatingSegmentsAxis` だけ。EDINET タクソノミ（jpcrp / jpigp）に地域別・主要顧客の軸は無く、`RevenuesFromExternalCustomersInformationForEachRegionTextBlock` / `InformationAboutGeographicalAreasIFRSTextBlock` / `InformationForEachOfMainCustomersTextBlock` などのテキストブロックだけ。1000 文字を超えるので値は残らない（§6.2）。地域別・主要顧客は取らない
- 各セグメントは会社独自のメンバー（`jpcrp030000-asr_E02144-000:AutomotiveReportableSegmentMember`。半期報は `jpcrp040300-ssr_…`、四半期報は `jpcrp040300-q3r_…`）。名前はその書類の label linkbase にしか無い。標準メンバー（`ReportableSegmentsMember` 等）も会社がラベルを付け替える（日立は「小計」「全社及び消去」）ので、ラベルは要素ではなく書類ごとに持つ（`xbrl_contexts.member_labels_json`）
- 売上高は外部顧客（`jpcrp_cor:RevenuesFromExternalCustomers` / `jpigp_cor:RevenueFromExternalCustomersIFRS` 等）・セグメント間（`TransactionsWithOtherSegments` / `IntersegmentRevenueIFRS`）・計（`jppfs_cor:NetSales` / `OperatingRevenue1` / `jpigp_cor:RevenueIFRS` / `NetSalesIFRS` / `Revenue2IFRS`）の 3 行。トヨタ・ソニーは計と外部顧客を会社独自の要素で出すので取れない（会社独自の要素はマッピングしない）
- セグメント利益の物差しは会社ごと: 日本基準は `jppfs_cor:OperatingIncome`（経常利益の会社もある）、IFRS は `OperatingProfitLossIFRS`（トヨタ・ソニー）/ `SegmentProfitLossIFRS`（日立の調整後営業利益）/ `ProfitLossBeforeTaxIFRS`（ソフトバンク G）/ `ProfitLossAttributableToOwnersOfParentIFRS`（伊藤忠）
- 単一セグメント（任天堂・武田）と米国基準（野村）はセグメントの数値 fact が無い（従業員数だけ）。0 行で正常
- 同じ軸に従業員数・設備投資・研究開発費も載る（`NumberOfEmployees` 等）。生 fact には残るが、今は正規化しない

### 14.2 生データ層

- EDINET の `keep_fact` は、当期でセグメント軸だけ（連結/単体の軸は付いてよい）の fact も残す（`app/edinet/xbrl.py`）。前期のセグメントは残さない。大株主・所有者別の軸（`MajorShareholdersAxis` / `MajorShareholdersVotingRightsAxis` / `ClassesOfSharesAxis`）も、要素名があるときだけ残す（KAB-266。`docs/jobs/edinet.md`「有報の大株主の状況・所有者別状況」）
- `xbrl_contexts.member_labels_json`: 連結/単体以外の次元メンバー → その書類の日本語ラベル（`{"prefix:Member": "自動車"}`）。無ければ NULL。パーサは fact の要素に加えて、context の次元メンバーのラベルも引く（`app/xbrl/parser.py parse_package`）
- 過去分は `edinet_xbrl.py --reparse` でダウンロードし直さないと入らない
- デプロイ: `jobs/migrate.py`（表と列）→ `python jobs/xbrl/xbrl_parse.py load-mappings`（SEG の科目とマッピング）。そのあとの取込から入る。取込済みの書類は `edinet_xbrl.py --reparse`

### 14.3 正規化層 `xbrl_segments`

列と索引は [`schema/xbrl.md`](../schema/xbrl.md) の `xbrl_segments`。

- 科目は `config/xbrl/std_items.yaml` の `statement: SEG`（`seg_net_sales` 計 / `seg_external_sales` 外部顧客への売上高 / `seg_profit` / `seg_assets`）、マッピングは `config/xbrl/mappings/segments.yaml`。`xbrl_financials` の正規化は SEG の科目とマッピングを読まない（同じ `jppfs_cor:NetSales` を取り合わない）
- `normalize_filing` の最後に `app/xbrl/segments.py normalize_segments` を呼ぶ（取込・`xbrl_normalize.py`・`--rebuild` の全部で作り直す）。TDnet の filing にも効く
- 選び方: 同じ (科目, 軸, メンバー, 連結, 期末) では mapping の `priority` が小さい方、同じなら期間が長い方（四半期報告書の累計）。IFRS の書類では `jppfs_*` を使わない（§6.4-7 と同じ）
- `member_kind`: 標準メンバーは `ReportableSegmentsMember`→subtotal、`TotalOfReportableSegmentsAndOthersMember`→total、`OperatingSegmentsNotIncluded…Member` / `OtherReportableSegmentsMember` / `OtherOperatingSegmentsAxisMember`→other、`ReconcilingItemsMember` / `UnallocatedAmountsAndEliminationMember` / `CorporateSharedMember`→reconciling。会社独自は名前に Elimination / Reconcil / Adjustment / Corporate があれば reconciling、ReportableSegment があれば segment、どちらも無ければ unknown

### 14.4 検証（2025-03 期の有報、IR Bank `list_segments` の百万円値）

| 会社 | 基準 | 外部顧客への売上高 | セグメント利益 | セグメント資産 |
|---|---|---|---|---|
| オリエンタルランド 4661 | 日本 | 3 セグメント一致 | 一致 | 一致 |
| JR 東日本 9020 | 日本 | 4 一致 | 一致 | 一致 |
| 信越化学 4063 | 日本 | 4 一致 | 一致 | 開示なし（IR Bank も null） |
| 日立 6501 | IFRS | 4 一致 | 一致（調整後営業利益） | 一致 |
| 伊藤忠 8001 | IFRS | 8 一致 | 有報の注記どおり（当社株主帰属当期純利益）。IR Bank の「営業利益」は有報に無い値 | 一致 |
| ソフトバンク G 9984 | IFRS | 3 一致 | 有報の注記どおり（税引前利益）。IR Bank は null | 開示なし |

既存の `xbrl_financials` は同じ 17 件で main と全行一致した（`keep_fact` を広げても次元付き fact は §6.4-1 で落ちる）。

## 15. 短信の原文 HTML（KAB-161）

銘柄タブ（`docs/screen/stock.md`）で短信を PDF と同じ並びで読むため、ZIP の HTML を `xbrl_documents` に入れる。数値の層とは別。パース・正規化の結果は変えない。

### 15.1 実データで分かったこと（2026-08-10〜10-23 の 304 件。種類ごとに最大 40 件）

- 短信 ZIP には `Summary/*-ixbrl.htm`（サマリー）と `Attachment/qualitative.htm`（タグの無い HTML。添付資料まるごと: 定性的情報・財務諸表・注記）がある。`qualitative.htm` は本決算・中間・四半期の短信でほぼ全件、REIT は 39 件中 28 件。訂正は訂正箇所だけの書類で欠けることがある
- 5933（2027/3 中間期）でサマリー＋`qualitative.htm` を Chromium で PDF にすると 12 ページ、数字 650 個と文字が元の PDF と一致した。違いはページのヘッダー・フッター、ロゴ、フォント。外の画像は TDnet の FASF マーク（`release.tdnet.info/img/mark1.gif`）だけ。`script` は 0 件
- HTML はよく縮むので gzip で持つ
- 数値タグの無い表（EBITDA、定性的情報の中のセグメント表、収益の分解など）は原文にしか無い
- 業績予想・配当予想の修正（`tse-rvfc…`）の ZIP はフォルダが無く、`*-ixbrl.htm` が ZIP の直下にある（`Summary/` も `qualitative.htm` も `Attachment/` も無い）。2026-10-25 時点で原文の無い TDnet の filing が 362 件あり、この形の ZIP は 0 件のまま毎時読み直されていた

### 15.2 表 `xbrl_documents`

列は [`schema/xbrl.md`](../schema/xbrl.md) の `xbrl_documents`。

`xbrl_filings.documents_status`: 原文を作ったかどうか。NULL（まだ）/ `stored` / `empty`（選べる HTML が 0 件）。

選び方（`app/xbrl/documents.py pick_documents`）: `Summary/` の `*-ixbrl.htm`（名前順）、続けて `qualitative.htm` を 1 つ。`qualitative.htm` が無いときは `Attachment/` の `*-ixbrl.htm` をファイル名順（`0101010-…` の並びが本表の順）。`Summary/` も `qualitative.htm` も `Attachment/` も無いフォルダなしの ZIP（`tse-rvfc` など）は、ZIP の中の `*-ixbrl.htm` を名前順に `summary` として入れる。それでも無ければ 0 行で、`documents_status='empty'` にする。

既存の表に入れない: `xbrl_filings` は取込・正規化・API で何度も引く表で、原文の BLOB を載せない。`xbrl_facts.value_text` は fact 単位で 1000 文字で切る（§1.2）。`qualitative.htm` はタグが無く fact にならない。

### 15.3 取込

- `xbrl_parse` が ZIP をパースした直後に、その filing の原文を作り直す（`parse_manifest_row`）。失敗してもパースの結果は変えない（ログ `documents failed …`）
- 同じ実行の最後に、原文の無い TDnet の filing（パース済み・失敗を含む。`parse_error='no-ixbrl'` は除く）を新しい順に `XBRL_DOCUMENTS_FILL_LIMIT`（既定 3000）件まで ZIP から埋める（`fill_missing_documents`）。デプロイ後の最初の毎時で既存分が入る。`documents_status='empty'` の filing は読み直さない。ZIP が無ければ数えて飛ばす。0 件だった ZIP は `documents empty doc_id=…` と出して `empty` に数える（ログ `documents stored=… empty=… missing_zip=… errors=…`）
- 元の HTML のまま保存し、除去は表示のとき（`sanitize_html`）。除去の決まりを変えても入れ直さない
- EDINET は対象外（ZIP の形が違う）

## 16. 過去の短信を PDF から HTML にする（KAB-164）

TDnet の ZIP は 2026-08-10 からしか無く、TDnet は約 1 か月で消す。それより前の回の原文は、IR BANK が保管する TDnet の開示 PDF から HTML に変換して `tdnet_pdf_documents` に入れる。数値は §16.5 で同じ HTML から `xbrl_financials` に入れる。

### 16.1 実データで分かったこと（2026-10-25）

- 番号は 1 つ: J-Quants の `statements.disclosure_no` = TDnet の docID = IR BANK の `doc_id`（14 桁）。TDnet XBRL ZIP の名前は `0812` + docID（5933 の 2026-10-20 は `081220261020553439` と `20261020553439`）
- PDF は `https://api.irbank.net/api/market-files/v1/tdnet/{docID}.pdf`（認証なし）。`statements` からランダムに 6 件、41 件とも取れた。2021 年の回もある。IR BANK に zip / xbrl / htm は無い（404）。TDnet の 1 か月より前の ZIP も 404
- 変換（`tools/tdnet_pdf/convert.py`）を 41 件（2021〜2026、20 社余り）で試した: pdfium の文字と比べて 75 万字中 5 字違い（`～` / `〜` の取り違え 4、読めない 1 字）。1 件 平均 20 ページ・gzip 後 43KB・3 秒。元の PDF 平均 626KB
- KAB-169 で読み込みを pdfplumber（pdfminer。Python）から pdfium（pypdfium2。C++）に替えた（`svg2`）。同じ 41 件で 合計 126.5 秒 → 16.6 秒（約 7.6 倍）、pdfium の文字と比べて 75 万字中 1 字違い（わざと捨てる `\ufffe`）。gzip 後は 約 7% 増える（曲線も描くため）。最大メモリは 71 ページで 約 217MB → 約 45MB、19 ページで 約 86MB → 約 30MB。旧版は時間の約 65% が PDF の解釈、約 35% が `dedupe_chars` / `extract_words` だった
- 手元の poppler（`pdftotext` / `pdftoppm`）は日本語の言語パックが無いと、フォントを埋め込んでいない PDF の文字を落とす（`Missing language pack for 'Adobe-Japan1'`）。突き合わせには使わない
- 出ないもの: 画像（会社のロゴ・定性的情報のグラフ・FASF マーク）

### 16.2 変換

pdfium（pypdfium2）でページごとに読む。文字は textpage の 1 文字ずつの原点（ベースライン）・ゆるい字の枠・大きさと、文字が属するテキストのオブジェクトのフォント名・太さ・色（オブジェクトごとに 1 回）。pdfium が足した空白・改行（generated）と制御文字・`\ufffe` は捨てる。pdfium は文字を UTF-16 の 1 単位ずつ返すので、同じテキストのオブジェクト内のサロゲートの組（`𠮷` など BMP の外）は 1 字に戻し、組にならないサロゲートは `U+FFFD` にする（UTF-8 に書けず `UnicodeEncodeError` で落ちていた。KAB-169、85110 の 20260212556179）。組の復元を通っても残り得るサロゲートは、変換結果を返す直前と `ingest_one` の `encode` 直前に `U+FFFD` 1 字へ置き換える（`encode('utf-8')` の strict は `UnicodeEncodeError`。`errors='replace'` は ASCII `?` になる。KAB-210）。置き換えは該当文字だけなので本文は欠けない。ベースラインが 1pt 以内の文字を 1 行にまとめ、左から並べ、字間が 1.2pt を超えるか書式（フォント・太さ・色・大きさ）が変われば語を切る。図形はページのオブジェクトの path を、Form XObject の入れ子（8 段まで）を辿って行列を掛け合わせ、SVG の `<path>`（直線・ベジェ曲線・閉じ）にする。塗りが白で線が無いものは捨てる。語の幅は `textLength`（`lengthAdjust="spacingAndGlyphs"`）で PDF に合わせる。フォント名に gothic / sans 等があれば sans-serif、それ以外は serif。太さ 600 以上か名前に bold / W6〜W9 等があれば太字。題名は 1 ページ目の行で `決算短信` / `業績予想` / `お知らせ` 等を含む最初のもの。変換器の版は `CONVERTER`（今は `svg2`。KAB-164 の pdfplumber 版は `svg1`）を行に残す。`svg1` の行は入れ直さない（見た目はほぼ同じ）

### 16.3 表 `tdnet_pdf_documents`

列は [`schema/xbrl.md`](../schema/xbrl.md) の `tdnet_pdf_documents`。

### 16.4 取込 `tools/tdnet_pdf/ingest.py`

- 対象: 指定した銘柄（`--code` 複数可、`--all` で全銘柄）の `statements` のうち、XBRL の原文（`xbrl_documents`。ZIP 名の後ろ 14 桁で照合）も、この表の `ok` / `missing` も無い回。新しい順。`failed` は `--retry-failed` のときだけ
- 1 件ずつ 取得 → 変換 → 保存（commit）。間隔は `--sleep`（既定 1 秒）、件数は `--limit`。IR BANK が 429 / 503 を返したら上限とみなして止める（終了コード 0）。途中で止めても次の実行で続きから
- 最後の 1 行: `tdnet_pdf stopped=<done|limit|rate_limited> ok=… missing=… failed=… remaining=…`。`remaining` はこの実行のあとに `targets()` を取り直した件数で、`failed`（この実行で failed にした件数）とは別。`--retry-failed` 無しでは failed 済みは残件に入らない。`stopped=done` なのに remaining>0 なら、実行開始時の対象に含まれなかった回（実行中に statements が増えた等）が残っている。`--dry-run` は対象の件数と先頭 10 件だけ
- 取った PDF が `/Linearized` の `/L`（ファイル全体の長さ）より短ければ、途中で切れたものとして `failed`（`truncated pdf`）。IR BANK に途中までしか無い PDF がある（79520 の 20260212556420 は 277,188 バイト中 76,224 バイト。pdfium も pdfminer も poppler も読めない）。旧変換器（pdfplumber）でも読めないのでフォールバックはせず `failed` のまま残し、`--retry-failed` で取り直す
- VPS で動かす（bot が銘柄を指定して毎日）。pypdfium2 は `requirements.txt` に入れた（KAB-169 で pdfplumber から替えた）

### 16.5 短信 PDF の数値（KAB-173）

`tdnet_pdf_documents.status='ok'` の HTML を、ZIP と同じ生の層に入れる。書き込むのは `sync_pdf_filing` だけ。`normalize_filing` と `rebuild_all` は変えない。決算速報の UI は変えない。

| 項目 | 中身 |
|---|---|
| `xbrl_filings.source` | `tdnet_pdf` |
| `doc_id` | 開示番号 14 桁（`statements.disclosure_no`） |
| `raw_path` | `tdnet_pdf:<開示番号>` |
| `raw_sha256` | `sha256(html の sha256 + ":" + READER_VERSION + ":" + 会計基準)` |
| 要素の名前空間 | `pdf-sum` / `pdf-bs` / `pdf-pl` / `pdf-cf`（カスタム。URI は `http://stock-analyzer.local/taxonomy/tdnet-pdf/`） |
| マッピング | `config/xbrl/mappings/tdnet_pdf.yaml`。`name` は `label_key` の結果 |

読める DocType は `{1Q|2Q|3Q|FY}FinancialStatements_{Consolidated|NonConsolidated}_{JP|IFRS}` と `EarnForecastRevision` と `DividendForecastRevision`。REIT、米国基準、`4Q` / `5Q`、日付の欠けは filing を作らない。1Q / 3Q に CF が無くても失敗にしない。PDF 1 ページ目の題名（`tdnet_pdf_documents.title`）に「速報値」がある開示は filing を作らない（`absent` / `flash-results`。failed に数えない）。元の短信と別の会計基準の財務諸表を追加したお知らせ（IFRS の短信に日本基準の財務諸表を追加した 2130・436A）は、`config/xbrl/pdf_other_standard.json` の開示番号の一覧にあるものだけ filing を作らない（`absent` / `other-standard`。規則では決めない。題名に「追加」があっても、同じ基準の表を足したお知らせがある）。訂正のお知らせは `is_amendment=True`（題名の「訂正」、または「決算短信」かつ「の修正について」「の一部修正」。本文の「（訂正前）」「（訂正後）」「（訂正版）」「<変更前>」「<変更後>」。業績予想・配当の「修正について」だけでは付けない。KAB-226）

HTML は svg1（`<line>`）でも svg2（`<path>`）でも、`<text>` の位置だけを見る。見出しと列の役割は `app/xbrl/pdf/_tanshin.py` の表。百万円は円（10^6 倍）、億円は 10^8 倍（「円」より先。8630 20231206599881）、百分率は `pure`（39.1 → 0.391）。設備投資の △ は負の fact のまま入れ、`std_items` の `sign: invert` で `capex` の行が正になる。「設備投資額」はマッピングしない。IFRS の連結の経常利益はマッピングしない（`standard: JPGAAP` だけ）。IFRS 提出会社の個別（#nc）表は JPGAAP として経常利益を採る（KAB-243）。IFRS 本表の売上原価・販管費は ZIP に合わせて正にする（印字の △ を外す）。

context は ZIP の短信サマリーと同じメンバー（`ResultMember` など）と、`CurrentYearDuration` / `CurrentAccumulatedQ2Duration` / `NextYearDuration` のようなトークンを使う。前期の実績は `is_normalizable_context` で fact ごと落とす。印字の行は、マッピングが無くても残った列については fact として残す。

読み方（ZIP・J-Quants と突き合わせて直したもの。`READER_VERSION=22`）:

- ページ番号（`- 6 -`）と柱（ページの上下の帯で 3 ページ以上同じ高さに出る行。2 行に分かれる柱も行ごと）は読む前に落とす。ダッシュが金額の「－」と読まれて幻の列になり、列見出しを吸っていた。ページ番号の判定は上下の帯の外だけ（表の途中の「- 104」を落とさない。KAB-220）
- 列見出しは、最初の数値の行のすぐ上に続く行だけ。上にたどって「。」を含む行・(注) / ※ / ＝ で始まる行・空白を除いて 40 字を超える語で切る（KAB-216。行全体ではない。武田の EPS 見出しは短い語が 1 行に並び 40 字を超える。KAB-224）。数値の無い行で ＝ を含む行（「事業利益 ＝ 売上収益 － …」）と、－ の後ろに文字が続く行（折り返しの「－ 売上原価」）は式の注記として見出しにしない（KAB-216 9983 20251007569462）。左が「前回/今回/増減/前期実績」で右が円銭などの単位だけの行は見出し帯に入れず行ラベルにする（KAB-243。6268 個別の「前回発表予想（Ａ）　円銭」。本表のあとに前回/今回が列見出しとして並ぶ修正表は列見出しのまま）。同じ行の語が 1 列に重なっても左から列に付け直さない。1 語の上段見出しも語の下の列にだけ付け、ほかの列へ広げない（#1071 で期間見出し「（自 …」がずれて本表が落ち、単位がずれて ROE・配当が 100 万倍になったので戻した）。数値の列の幅（左端は数値の左端の最小）に重なる語は、重なりが正の列に載せる。端だけ接する隣の列には載せない（KAB-213 の経常利益）。語が ％ 列を挟んで金額列を 2 つ以上またぐときは、`textLength / 字数` で 1 文字ごとの x を推定し、文字を最寄りの金額列へ分ける（KAB-238。6268 の「親会社の所有者に基本的１株当たり」が NP と EPS の 1 語になっている書式）。隣り合う金額列にまたがる親見出し（「年間配当金」）と、金額列と％列だけにまたがる語（1377 の純利益）と、表の左（行名側）から伸びる見出し（7865「2025年1月期第1四半期業績予想（日付）」）と、第1四半期・通期・業績予想を含む語は、語全体を金額列に載せる。％ だけの列は分け先にしない。列に重ならない 1 字は右隣の金額列へ付ける（KAB-236。9501 の「る」「１」を中心の最寄りにすると経常損益る・帰属す1 になる）。単位の 1 字（円・銭・%）は右隣へ飛ばさず中心の最寄り（％ が経常利益列に、円銭が ROE 列に載ると金額が落ちる / 100 万倍になる）。それ以外の重ならない語は中心がいちばん近い列（80pt 以内）。右端だけで当てると中央寄せの見出しが付かず「右端が当期」の決め打ちになる（伊藤忠は当期が左、増減額が右）。表の右上の注記（「％表示は、対前期増減率」）と「(注)」の行は見出しにしない
- 金額は、直前が行頭・空白・△▲・～ のときだけ取る（語の途中の数字や電話番号の末尾は金額にしない。KAB-216）。`※` / `※n` は後ろに空白があるときだけ消してから金額を取る（KAB-214）。配当の「40 円」の円は金額の飾り（KAB-213）。「20円00銭」は 20.00 の 1 数値（語が分かれても。KAB-237）。1 語の「801円98銭」も 801.98（KAB-233）。円列と隣の銭列は 1 つの 1 株当たり値にする（KAB-251。3608 の EPS 35円21銭、3944・7590 の FEPS）。「15円00銭」の列の右端は円の数字に合わせ、未定の列と揃える（9507）。未定セルが無い EPS の桁違い（7865 20241126529281 の 65.53 と △41.80）は束ねない（「1」「株当たり」が列末尾に回ると名寄せできない）。「未定」はダッシュと同じ欠け（列見出しにしない）
- 「（訂正前）」から「（訂正後）」の手前までを捨て、訂正後の表を使う（KAB-212）
- 数値より上にずれた行名（数値のすぐ上で、列より左からだけ始まる行）は見出しにせず次の数値の行の行名にする。数値の下にずれた「今回予定」「前回予定」「実績」（列より左だけ、直前の数値行の方が次の数値行より近い）は直前の行の行名（KAB-237）。値だけの行のあと、「(予想)」「額（△は減少）」「増減額（△は減少）」は前の行の続き。前回/今回/増減/前期で始まる行は新しい行名（pending の連結もしない。KAB-236。CF の折り返し「増減額（△は減少）」は増減の行の役割にしない）。業績予想・配当予想の修正は Forecast の行だけ残す（前期実績を今期末の実績にしない）。pdf-sum の行名の先頭の「連結」「個別」は対応表に渡す前に落とす（KAB-213）。目次の点線は捨てるが、空白区切りの「－」だけの金額行は残す（KAB-236。9501 の前回予想が未定）
- 修正開示の会計基準は、同じ銘柄の `statements`（`*_IFRS` / `*_JP`）のうち日付が近いものから取る。基準は指紋に含める
- 業績予想の修正の連結/単体（KAB-247。`READER_VERSION=9`）: `EarnForecastRevision` の書類種別には `Consolidated` / `NonConsolidated` が無いので、従来は連結になっていた。見出しに「個別」「単体」だけあれば単体、「連結」だけあれば連結（従来どおり）。どちらも無いときは、同じ銘柄の通常の短信のうち日付が近い 1 件の連結/非連結を使う（会計基準の引き継ぎと同じ）。近い短信が非連結なら単体（7865 の 1Q/3Q 修正。表の見出しに「個別」が無く、本文が「非連結」の短信を引用しているだけ）。近い短信が連結なら連結のまま。通常の短信が無ければ連結のまま。連結の短信を出している会社を、古い非連結だけで単体にしない
- 列の役割は日付（「至 2026年6月30日」）を先に、無ければ年月（「2027年3月期」「2026年6月末」、和暦「令和8年」「8年6月期」）。語が分かれた見出し（「2026年」「6月末」）はつないで読む。期間の長さは語（第1四半期・中間）で決める
- 期の判定（KAB-228。`READER_VERSION=7`）:
  - 「令和」の付かない 1〜2 桁の年は、令和の省略（「8年6月期」= 2026）と西暦の下 2 桁（6268「22年12月期」= 2022）のうち、書類の年度末に近い方（KAB-241）。「令和」が付けば令和
  - 年をずらすとき（前期・翌期の期末、前年同期）、月末は月末のまま（6506 の 2023年2月期の翌期末は 2024-02-29。KAB-239）。共有の `app.xbrl.context.shift_years` は EDINET も使うので変えず、`_tanshin._shift_years` を使う
  - 業績予想の修正（`fc_rev`）は、列にも行にも期の語が無ければ表の見出しの期を使う。見出しの日付の範囲（「2021年9月1日～2022年2月28日」）を先に、期首からの月数（3・6・9・12）で第1〜第3四半期累計・通期を決める（20 日決算も同じ）。日付が無ければ「第2四半期（累計）」「通期」の語。見出しに期が無ければ題名。累計と通期の両方が書いてあって決まらなければ入れない（通期と決めつけて上書きしない。7513・7865 で累計の値が通期の予想になっていた）
- 本表で捨てる列: 増減・比率（%・前年同期比・売上収益比）・3 か月だけ（「第2四半期連結会計期間」）・予想（本表の後ろの「業績予想の修正」表）。同じ役割の列が 2 つなら見出しで決まった方を残し、どちらも見出しで決まっていれば両方捨てる。ダッシュだけの列は役割を付けない
- △ だけ右端が数 pt ずれる列（JT）は、同じ行に並ばない 8pt 以内の列を 1 列にまとめる。見出し語の範囲（x0–x1）に右端が入る数値は、同じ行に 2 つ以上入る見出し（またがり。KAB-238）を除き、その見出しに入った数値の最大 x1 へ寄せてから列を決める（KAB-229。7865 の小さい業績予想修正表。見出しの右端へ寄せると隣の ％ 列まで伸びる）。単位の 1 字（円・銭・%）と百万円等は寄せ先にしない（KAB-236 の ROE）
- 本表は決算短信ごとに BS・PL・CF の最初の 1 つ（数値の行があるもの）。見出しは行頭から（「自動車等セグメントと…区分した連結損益計算書」は本表でない）。先頭の ①–⑳・`(1)`・`1.` は正規化の前に落とす（① は NFKC で 1 になり `1.` に当たらない。KAB-234）。連結の短信は「連結」のある見出し、非連結は無い見出し（子会社・単体の表を読まない）。見出しの後ろの補足（「[第1四半期連結累計期間(…3ヵ月間)]」）は外して判定。ほかの「…計算書」「…対照表」「…明細書」（事業費明細書、製造原価明細書など）と、後ろの注記（「(…に関する注記)」、文章の行）でブロックを切る。「純損益計算書」は PL。「包括利益計算書」は、同じ書類に損益計算書が無いときだけ PL として読む（KAB-259。IFRS の要約中間/四半期連結包括利益計算書）
- サマリーは起点から 2 ページ、ページをまたがない（目次のページ番号を予想の値にしない）。起点は最初のサマリー見出し（「(1)連結経営成績」等）があるページ。通常の短信は 0。訂正理由書のあとに全文がある書類は 3 ページ目以降（KAB-226 / KAB-235）。「(2)個別財政状態」「(参考)個別業績」は単体の値として読む。実績の表で期の読めない行（「(金融を除く)」）は読まない。同じ表で同じ科目・同じ期に違う値が出たら両方捨てる
- 配当は ZIP と同じく単体（`is_consolidated=0`）。context の ref に `_NonConsolidated` を付け、連結の本表の context と取り違えない。列見出しが時点（「第１四半期末」「年間」）だけの列は 1 株当たり配当金。単位の注記も「円銭」も無い配当の修正では、時点だけの列を 1 株当たりとして読む（KAB-223 9983 20221215579485。「１株当たり配当金」は期末列の上にしか無い）。時点が「第 / 2 / 四半期末」と語が分かれていても、つないだ時点で列の役割を決める（8511 20260212556179）。親見出しが「年間配当金（円）」のように単位の括弧付きで期末列だけに付くときは、括弧を外して 1 株当たりとして読む（KAB-245。`READER_VERSION=8`）。親見出しに分かれた「期」「末」が張り付いて「年間配当金期末」になった列は、時点を戻して 1 株当たりとして読む（KAB-251 9507）。「配当金総額（百万円）」「配当性向（%）」は外さない。配当予想の修正のほか、「予定配当額について」「中間配当（予定）について」も読む（KAB-237）。業績予想と配当予想の同時修正（`EarnForecastRevision`）でも「配当予想の修正」ブロックを読む（KAB-231。題名の「業績予想及び配当予想の修正」は `fc_rev` が先に取る）。行の「今回予定」「前回予定」「今回決定」は今回/前回予想と同じ。短信の「配当の状況」は当たらない
- 「銭」「円銭」を含む見出しは 1 株当たり。行名の「金銭」（金銭の信託、買入金銭債権など）は単位の判定に使わない（表の単位のまま。KAB-452。`READER_VERSION=22`）
- レンジ予想は 3 行（「下限 / ～ / 上限」「下限～ / ダッシュ / 上限」「下限 / 行名 / ～上限」）を 1 行の下限・上限にする。「5,100～ 30.9～」のように語をまたいで 1 つのレンジと読まない
- 行名は `label_key` でそろえる: 「又は…損失」だけ落とす（末尾、または「の帰属」の直前。「営業利益又は営業損失(△)」→ 営業利益。「四半期利益又は損失(△)の帰属 親会社の所有者」の「の帰属…」は残す。KAB-240）、「(△…)」を落とす、先頭の「(内訳)」を落とす（KAB-260）、「営業損益」「経常損益」「当期純損益」は利益の名前、末尾の `(A)` / `(B)` を落とす、四半期・中間の利益は当期の名前、「X利益の帰属 親会社の所有者」→ 親会社の所有者に帰属する当期利益、「営業活動に関する／から得た…(純額)」→ 営業活動によるキャッシュ・フロー、「四半期末残高」→ 期末残高、「1株当り」→「1株当たり」（KAB-251）。列見出しの「(百万円)」「(円)」は単位（KAB-242）。見出しが裸の「円」だけの列と「株当」を含む行名は 1 株当たり（表の注記の百万円を採らない。KAB-251）。本表で「株当」を含む行のすぐ下に続く「基本的」「希薄化後」「潜在株式調整後」「継続事業」「非継続事業」「合計」（と、その組み合わせ・「(円)」付き）だけの行も 1 株当たり（KAB-432。数値の無い見出し「1株当たり中間利益」は最初の行「基本的」にしか付かないので、続く「希薄化後」が表の百万円で 100 万倍になっていた。行名は変えない）。1 株当たりの値が 1e5 以上なら入れない。折り返した行名は、前の行が「及び」等で終わるか、値だけの行の後なら前の行の続き。「損失」で始まる数値の無い行は前の行名の尾。数値の無い「（内訳）」は区分見出しとして落とす（KAB-260。親会社株主に帰属する行の頭に付くと名寄せできない）。数値の無い「…の帰属」は、次の数値行が「に帰属する」を含むときだけ区分見出しとして落とす（KAB-264。81330。次が「親会社の所有者」だけのときは残して連結する。KAB-240）。数値の無い「営業/投資/財務活動によるキャッシュ・フロー」と BS の「資産」「流動資産」等と PL の「収益」（IFRS の内訳の親。KAB-261）は区分見出しとして落とす（KAB-216）。数字を含む行名は、「株」を含むか、先頭の「1年内」「1年以内」を除くと数字が無いときだけ読む（注記番号の付いた「その他収益5,7」は入れない。「1年内返済予定の長期借入金」は読む）
- サマリーの「第2四半期」（末尾が「末」でないもの）は累計 Q2（KAB-215）
- 本表ページが文字化けした PDF は `parsed` のまま残し、`parse_error` に `garbled pages=3-14` のように印を付ける。1〜2 ページ目のサマリー（売上高・EPS）は入れる。文字が図形だけで表が 1 つも無いときだけ `failed`（`no in-scope table`）。柱を落としたあとの字数が少なく、図形（`<path>` / `<line>`）が多いページも同じ印を付ける（KAB-227。アウトライン化されたページ。取れた値は残す）。2 ページ目以降で、柱・ページ番号を除いた文字が無いページは `textless pages=4-6`（KAB-262。画像・アウトラインで `<text>` が 0。garbled と同じく `parsed` のまま、サマリーの値は入れる。OCR はしない）。`tools/tdnet_pdf/financials.py` の最後の行に `garbled=` と `textless=` を出す（KAB-218）
- CF の「現金及び現金同等物の期末残高」「期首残高」は時点（`period_start` 空。KAB-263）。TDnet ZIP / EDINET の `CashAndCashEquivalents` に合わせる。営業・投資・財務 CF は期間のまま

突き合わせの結果（2026-10-26。`verify_financials.py`。共有データの TDnet ZIP と、同じ回の IR BANK の PDF）:

| 組 | 一致 | 不一致 | ZIP だけ | PDF だけ |
|---|---|---|---|---|
| 開発に使った 85 組（日本基準 連結・非連結、IFRS 22） | 2,817 | 0 | 63 | 17 |
| 使っていない 57 組（日本基準） | 1,886 | 0 | 122 | 41 |
| 使っていない 30 組（IFRS） | 909 | 0 | 54 | 9 |

「ZIP だけ」は減価償却費・設備投資（PDF の CF に行が無い、行名が違う）、研究開発費、決算期を変えた 37500 など。開発の組の 63 は、ツールが `expected_zip_only` に分ける 1Q・3Q の CF 14 件を含む（ZIP に CF があるので取りこぼし）。「PDF だけ」は設備投資（ZIP の要素名が対応表に無い）と 37500。J-Quants（`statements.raw_json`、ゴールデンデータの 40 社余り 177 件）とは 2,176 件一致・1 件違い（ソニーの期末の現金。PDF は CF の期末残高、J-Quants は BS の額）。REIT・米国基準は読まない。日立の「調整後営業利益」は営業利益にしない（ZIP にも無い）。

有報・半期報（EDINET）とも比べた（2026-10-26。ゴールデンデータの 43 銘柄、同じ期が両方にある 80 期）。実績 1,647 件一致・16 件違い。違いは読み取りではなく書類・定義の差: 短信の出し直し（武田。当初の短信と有報は別の数字）、短信のあと有報で数字が変わった回（26740 の BS）、売上高の定義（イオンは PDF が営業収益・EDINET の対応表が内訳の売上高、SOMPO は PDF が PL の保険収益・EDINET が注記の収益）、短信と有報で基準が違う（東京海上の減価償却費。短信は日本基準、有報は IFRS）、ROE（44930。短信の印字どおり）、ソニーの期末の現金。同じ期の短信が 2 回出たときは PDF の filing も 2 つになる（ZIP と同じ。使う側が新しい方を選ぶ）。

`tools/tdnet_pdf/financials.py` が新しい順に最大 `--limit`（既定 2000）件。cron では動かさない。新しい短信は ZIP があり `tools/tdnet_pdf/ingest.py` が PDF を取らないので、過去の PDF を入れたあとに手で流す（`--limit 100000` で全件）。後から ZIP が来た回の PDF filing は ZIP の取込が消す。指紋が同じ `parsed` / `failed` は HTML を読まない（`unchanged`）ので何度流してもよい。`READER_VERSION` を上げるか会計基準が後から決まると指紋がずれ、次の実行が HTML を読み直す。`--reparse` は指紋を無視する。マッピングだけ変えたときは `jobs/xbrl/xbrl_normalize.py --rebuild` で足りる。

最後に、その実行で `parsed` / `retired` / `failed` になった開示の銘柄の `xbrl_pdf_matches` を付け直す（§18。`--no-matches` で付けない）。

`--batch N`（例 500）は N 件ごとに区切り、区切りごとに `tdnet_pdf_financials batch done=500/100000 parsed=… unchanged=… failed=… retired=… absent=… garbled=… textless=… left=99500` を 1 行出す（KAB-435。数は累計、`left` は今回の残り）。コミットは `--batch` があっても 1 件ごとなので、途中で止めて流し直せば取り込み済みは `unchanged` で飛ばして続きから進む。`database is locked` で落ちた回は区切りの終わりに 5 秒待って 1 回やり直し、それでも落ちた回だけ `failed` に数える（`pdf failed disclosure_no=…` の行が出る。filing は残らないので次の実行がまた試す）。`--batch` なしは区切らず、やり直しもしない。最後の集計行はどちらも同じ。

ZIP の `parse_manifest_row` は、パースが `parsed` のあと、失敗を `mark_failed` する `try` の外で `sync_pdf_filing(db, doc_id の後ろ 14 桁)` を呼ぶ。PDF 側の例外は ZIP を `failed` にしない。`parse_status='parsed'` の `source='tdnet'` がある 14 桁は PDF filing を消す。

ZIP との付き合わせは `tools/tdnet_pdf/verify_financials.py` がスクラッチの SQLite で行う（本番 `stock.db` へは書かない）。提出日は `statements.disclosed_date` / `disclosed_time` を優先し、無ければ ZIP 名の先頭 8 桁から取る（開示番号からは作らない）。`statements` に J-Quants の日付がそろっているときは ZIP の期末で上書きしない（KAB-219）。テストは本番 DB も IR BANK も呼ばない。


### 16.6 短信 PDF の回帰チェック（KAB-244）

`app/xbrl/pdf/`・`config/xbrl/mappings/tdnet_pdf.yaml`・`tools/tdnet_pdf/financials.py` を変える PR は、`tools/tdnet_pdf/golden_check.py` を通す。#1071 は J-Quants との一致を 1,795 → 1,565 に落としたが、合成のテストは全部通っていた。実データの検証済みの値で止める。

- **正解データ** `tests/golden/tdnet_pdf/expected.json`: XBRL 検証 bot の 2〜3 回目（19 銘柄）の 407 開示。`values` は読み取りの行のうち J-Quants と一致を確かめた値だけ（検証 bot の `cmp_jq.json` も `match`、かつ `build` で J-Quants の値と突き合わせて一致）。`evidence` に J-Quants の項目名。`known_gaps` は J-Quants と合わない項目で、直ると「一致するようになった」と出る。2021-10 より前の 12 開示は J-Quants（Light）が返さないので入れていない。EDINET と一致した値はまだ入れていない（`evidence: "edinet:…"` を受ける形にはしてある）
- **HTML**: 全件は `data/tdnet_pdf_golden/html/{開示番号}.html.gz` と `titles.json`（repo に入れない）。`fetch` が IR BANK の PDF を `convert.py` で変換して作る（PDF は残さない）。`html_sha256` と違えば知らせる。原因の種類ごとの小さな組（38 開示・約 1.3MB。`html/classes.json` に種類）は `tests/golden/tdnet_pdf/html/` に入れ、`tests/xbrl/test_tdnet_pdf_golden.py` が CI で回す
- **読み方**: 一時ファイルの SQLite に `statements`（J-Quants の日付）と `tdnet_pdf_documents`（HTML と題名）を入れ、`sync_pdf_filing` を開示番号の順に流す。会計基準は同じ銘柄の前の filing から引き継ぐので、開示 1 件だけでは結果が変わる。小さな組には、業績・配当の修正の直前の短信も入れてある
- **判定**: 正解の値が消えた・変わったら exit 1。`--base-repo`（main の worktree）を付けると、正解データに無い行も含めた全行の差（消えた / 変わった / 増えた / filing の状態）を出す。PR 本文には `--md` の出力を貼る
- **例外**: 正解の値は J-Quants と一致した値なので、変わるのは原則退行。J-Quants 側の誤りなどで変えるときは、PR 本文に一覧と理由を書き、同じ PR で `expected.json` を直す
- **作り直し**: 検証 bot の新しい突き合わせ（`cmp_jq.json`）が来たら `build` で作り直す。差分は 1 値 1 行なので PR で読める
- **J-Quants の業績予想の修正の欄**（KAB-246）: `CurPerType=1Q / 3Q` の修正は、第1・第3四半期累計の予想を末尾なしの欄（`FSales` / `FNCSales` など）に入れて返す。`jquants_checks` はこれを期末 `CurPerEn` の Q1 / Q3 累計（`F*` は連結、`FNC*` は単体）として突き合わせ、通期にしない。`2Q` の修正は末尾なしが通期、`2Q` の欄が上期。`CurPerType` が分からない修正の末尾なしの欄は突き合わせない。検証 bot の `cmp_jq.py` は古い規則（通期扱い）のままなので、`build` は bot の `match` を値の一致の裏付けとしてだけ使う

```bash
# HTML を用意する（初回だけ。IR BANK に 1 秒おき）
python tools/tdnet_pdf/golden_check.py fetch
# 今のチェックアウトを正解データと比べ、main との全行の差も出す
git worktree add /tmp/sa-main origin/main
python tools/tdnet_pdf/golden_check.py check --base-repo /tmp/sa-main --md /tmp/golden.md
# 正解データを作り直す（保守用。J-Quants の /fins/summary の行の JSON が要る）
python tools/tdnet_pdf/golden_check.py build --statements fins_summary.json \
  --cmp-jq r2_prev5=.../cmp_jq_prev5.json --cmp-jq r2_new5=.../cmp_jq_new5.json --cmp-jq r3=.../cmp_jq.json
```

## 17. 財務3表を出どころをまたいで並べる（`GET /api/stock/{code}/financials`）

`app/xbrl/three_statements.py`。テーブルもバッチも足さず、読むたびに 1 銘柄の `xbrl_facts` から組み立てる（filing_id で絞る。連結と個別は 1 回の読み込みで分ける）。PDF だけの期の対応は §18 の `xbrl_pdf_matches` にも残す（API は読まない）。API の形は `docs/screen/stock.md` の「API」、MCP `financial_statements`（`mcp/earnings_tools.py`）はこの API を TSV にするだけ。

**材料**: `source` が `edinet` / `tdnet` / `tdnet_pdf`、`doc_type` が `annual` / `semiannual` / `quarterly` の `parsed` の filing。当期（`relative_period='current'`）の fact で、次元が連結/単体のメンバーだけのもの（`is_primary_statement` と同じ）。同じ filing・同じ連結区分で同じ要素に当期の期間の fact が複数あるとき（四半期報告書・半期報告書・短信 XBRL の期首からの累計と 3 か月）は、`start_date` がいちばん早いもの（期首からの累計）だけを使う（表の振り分けは捨てる前の並びで決める）。連結/個別は `xbrl_contexts.is_consolidated`（有報の単体は `NonConsolidatedMember` の軸付きで 0、PDF は `_NonConsolidated` の context で 0）。DEI が「連結財務諸表を作っていない」の有報は全部個別（`normalize` と同じ）。`consolidated=auto`（API の既定）は、連結の fact でどれかの表が組める filing が 1 つでもあれば連結を主にし、個別の材料もあれば、その表の連結の列のいちばん古い期より前・いちばん新しい期より後の期を個別の列で埋める（途中から連結を始めた会社・連結をやめた会社）。連結の列の間の期は個別で埋めない（連結の会社の有報の個別を混ぜない）。列は連結・個別それぞれで組んでから期でまとめるので、短信 PDF の突き合わせは同じ方の中だけ。科目名は連結のほうを採る。連結の材料が無ければ個別だけ。`jpdei_cor` / `jpcrp_cor` / `tse-ed-t` / `pdf-sum` は出さない。XBRL は `jppfs_cor` / `jpigp_cor` と会社独自（`is_custom`）だけ。

**表の振り分け**: PDF は名前空間（`pdf-bs` / `pdf-pl` / `pdf-cf`）。XBRL は、表示リンクの定義がある書類（`presentation_status='ok'`。§4.4）は定義で、無い書類（NULL / `none` / `gone` / `failed`）は下の推定で決める。

- **定義で分ける**: その書類の `xbrl_presentation_roles` の区分を、連結/個別（`is_consolidated`）と表ごとにまとめ、区分の要素に当たる fact をその表に入れる（行のキーは標準が `local_name`、`is_custom` が `ext:<local_name>`）。区分の連結/個別は fact の連結区分（下の **材料**）にそのまま当てる。同じ表・同じ連結区分に IFRS の区分と日本基準の区分の両方があれば（1 つの書類に 2 組の表）、書類の会計基準（`accounting_standard` が `IFRS` なら IFRS の区分）に合う方だけを使う。片方しか無ければそれを使う（IFRS の会社の個別は日本基準の区分だけ）。PL は損益計算書の区分と包括利益計算書の区分を、区分の書類の中の順（`seq`）でつなぐ。ただし包括利益計算書だけの区分（区分名に `StatementOfComprehensiveIncome` があり `SingleStatement` の無いもの）は、損益計算書の区分の後ろに置く（pre.xml の区分の順は印字の順ではなく、包括利益計算書が先に書かれた書類がある）。行の並びは印字順（fact の id 順）を土台にする。印字順と定義の順が合う fact のいちばん長い並びを本体とし、本体の最初から最後までの範囲にある fact は、定義の順と合わなくても印字順のまま（会社の表示リンクで合計が内訳より前にある、同じ要素が見出しの位置にも出る、といった書類がある。本番で 4052・4483・5020）。範囲の外にある fact は、定義の位置で本体の間に入れる: 同じ fact が前の表で先に印字され、id がその表の位置にあるもの（株主資本等変動計算書の当期末残高にある純資産の合計。PL が BS より前の海運で純資産が BS の先頭に来る。PL にある税引前利益は CF の先頭）。PL・CF の時点の fact（CF の期末の現金）も定義の位置で入れる。定義の位置は、同じ要素が 2 か所に出るときは最初、当期の時点の fact は `periodEndLabel` の出現、無ければ `periodStartLabel` でない最後の出現（期首の位置には置かない。4568 は `periodEndLabel` が別の要素）。時点・期間では絞らない（投資法人の PL は前期繰越利益・当期未処分利益の時点の要素を印字する）。同じ fact が 2 つの表に入ることがある（税引前利益が PL と CF）。定義に無い表は、その書類では無い（推定で補わない。連結の 3 表の区分が無い IFRS の有報は、その書類に連結の表が無い）
- **推定で分ける**（定義の無い書類）: fact の id 順（印字順）で決める。BS は合計要素（`Assets` / `LiabilitiesAndNetAssets` / IFRS の `…IFRS`）を含む時点の fact の連なりのうちいちばん長いもの。CF は要素名が `…OpeCF` / `…InvCF` / `…FinCF`・`NetCashProvidedByUsedIn…`・`NetIncreaseDecreaseInCashAndCashEquivalents…`・`EffectOfExchangeRateChange…` の最初から最後まで（`NetCashProvidedByUsedIn…` が無ければ CF なし。四半期報告書の注記の減価償却費を CF にしない）と、すぐ後ろに続く間の、新規連結・連結除外・合併などに伴う現金及び現金同等物の増減（要素名が `CCE` で終わるか `…InCashAndCashEquivalents…` を含む。期間。会社独自の要素も）と `CashAndCashEquivalents…` の時点（期末残高）。どれでもない fact が来たらそこで終わる（後ろの注記の販管費・棚卸資産の内訳などは入らない）。`CashAndCashEquivalents` で始まる期間の要素（IFRS の `CashAndCashEquivalentsInterestIncomeIFRS`）は伸ばす対象にしない。PL は BS の後ろ・CF の前の期間の fact のうち、最後の `ProfitLoss` / `NetIncome` / `ComprehensiveIncome` / `…PerShare` までで、その後ろ（株主資本等変動計算書の合計欄、注記の販管費内訳）は捨てる。PL が BS より前に印字される書類（海運の日本基準・武田などの IFRS の有報、一部の IFRS の短信 XBRL）は、BS より前の期間の fact の方がこの終わりの目印の要素を多く含むとき、そちらを PL にする（同じ目印までで切るので、PL と BS の間の株主資本等変動計算書は入らない）。そのとき先頭の経営指標等（`…SummaryOfBusinessResults` / `…KeyFinancialData`）と株式の保有状況（`…InvestmentEquitySecurities…`。会社独自の要素で入る）は除く。BS より前に別の BS の合計要素があるとき（1 つの書類に日本基準と IFRS の 2 組の表）は、選んだ BS の後ろのまま。BS が CF の後ろにある並びは、CF より前の BS 以外の期間の fact から取る。

**行のキー**: 標準要素は `local_name`、会社独自は `ext:<local_name>`（有報と短信 XBRL で名前空間が違っても同じ行にする）、対応が付かない PDF の行は `pdf:<local_name>`。タグが替わって 2 行に割れた同じ科目は、下の「タグが替わって割れた行をまとめる」で新しい期の行のキーにまとめる。

**PDF の行と要素の対応**: 同じ期末に PDF と XBRL の両方がある回（XBRL は 有報 > 短信 XBRL の新しい方）で、表ごとに絶対値の一致で LCS（印字順を保つ最長の対応）を取る。0 は対応させない。単位の丸めが違う（有報が千円、短信が百万円）ときは粒度の差まで同じとみなす。LCS で付かない行は、その表で値が 1 回だけ・XBRL 全体でも要素が 1 つだけ・粒度の 100 倍以上なら表をまたいで付ける（CF の税引前利益、IFRS の CF 期末の現金）。ただし PL・CF の行は、その表に振り分けられていない時点（instant）の要素には付けない。BS の残高と値がたまたま同じだっただけの行を止める（8179 で CF の期首残高が流動負債合計に付き、CF に流動負債合計の行が出ていた）。CF の行が `CashAndCashEquivalents` で始まる要素に付くのは止めない（IFRS は BS の現金と CF の期末残高が同じ要素）。PDF の行が時点か期間かは見ない（期末残高の行を期間で読んでいる PDF がある）。PL の行は、CF の要素（要素名が上の CF の目印に当たるもの）にも付けない。CF に振り分けられたかどうかでは見ない（1 つの書類に日本基準と IFRS の 2 組の表があると CF の範囲に IFRS の PL が入る）（9206 は通期の短信 PDF の PL の後ろに事業費明細書の行が続き、その 1 行が CF の減価償却費と同じ値で、有報の期の PL に減価償却費の行が出ていた）。CF の行が PL の要素に付くのは止めない。PDF と XBRL で符号が逆なら向きも覚える。XBRL の振り分けでその表に入らない要素が PDF で付いたら、その会社ではその表の要素として足す（XBRL だけの期の CF に税引前利益が出る）。

**PDF の行名の土台**: 覚えるとき・当てるとき・名前で当てるときは、PDF の `local_name`（`xbrl_facts` はそのまま）から `#n` を外し、読み取りが行名の頭に付けた区分見出しを外し、NFKC と「又は…（△）」「（△…）」の除去をした名前（土台）を使い、同じ表で土台が同じ行に印字順で `#n` を付け直す。外す見出しは BS が 流動資産・有形固定資産・無形固定資産・投資その他の資産・繰延資産・非流動資産・流動負債・固定負債・非流動負債・株主資本・その他の包括利益累計額・評価・換算差額等、PL が 営業外収益・営業外費用・特別利益・特別損失、CF が 営業・投資・財務活動によるキャッシュ・フロー（見出しの後ろの「:」も外す。見出しが重なっていれば繰り返す）。後ろが「合計」「小計」「計」（合計の行）、「#」、括弧・「、」・罫線・「の」（「のれん」は外す）・「に」「へ」「を」「と」・「及び」「並びに」で始まるもの、「等」だけのものは外さない（`有形固定資産(純額)`、`有形固定資産の取得による支出`、`その他の包括利益累計額の内訳…`）。見出しが付くのは区分の最初の科目で、どの科目が最初に来るかは期で変わるので、外さないと同じ科目が期で別の名前になる。画面の行名（下の「行の並びと名前」）と対応が付かない行のキー `pdf:<local_name>` は変えない。

**2 回目の印字**: PL で、土台が同じで値も同じ（0 でない、「その他」でない）行が前にあり、その会社の同じ期末に XBRL がある回で、2 行のうち片方だけが値で要素に付いたとき、その土台を「2 回印字される名前」として覚える（IFRS の損益計算書の最後と包括利益計算書の最初の「当期利益」。XBRL の要素は 1 つ）。どの回かで覚えた名前の 2 回目以降で、前の行と値が同じものは前の行の写しとして扱い、前の行と同じ要素にする（`#n` の番号も付けない）。写しは「同じ期で 2 行が同じ要素」の安全弁にかけない。値が違えば写しにしない（3 か月の値を並べた会社など）。2 行とも別々の要素に付いた名前（非支配持分の当期利益と包括利益がたまたま同じ値）は写しにしない。表では写しの行を出さない（前の行と同じ値）。`xbrl_pdf_matches` には写しの行も前の行と同じ `element_key` で残る。BS・CF は、同じ名前の別の科目（区分ごとの「貸倒引当金」など）がたまたま同じ値になることがあるので写しにしない。

**区分の中の対応**: BS・PL・CF の PDF の行と XBRL の要素に、並びで「その行より後ろで最初に来る区分の合計」を区分として持たせる（合計の行そのものはその区分）。合計は、BS が下の「区分」の大区分と区分の合計の要素、CF が営業・投資・財務の合計、PL が `GrossProfit` / `OperatingIncome` / `NonOperatingIncome` / `NonOperatingExpenses` / `ExtraordinaryIncome` / `ExtraordinaryLoss`（日本基準。IFRS の PL は合計の要素が無いので区分なし）。PDF の行の区分は、その表で値で要素が付いた合計の行（第 4 段では、語幹で付いた合計の行も）で決め、行名の文字列からは決めない（合計の行に対応が付かなければ区分なし）。この区分は PDF との突き合わせにだけ使う（PDF には表示リンクが無いので、PDF の行の区分は並びから決めるしかなく、比べる相手の XBRL の側も同じ決め方にそろえる）。表に出す区分と、行をまとめる判定の区分は下の **区分** で、表示リンクの見出しから決める。同じ期末に PDF と XBRL がある回では、値で付いた行を（区分、土台）でも覚える（その表の区分の中でその土台が 1 行だけのとき）。

**PDF だけの期**: PDF の行名の土台（`#n` 付き）で覚えた対応を引く。回は「通期どうし・四半期どうし」を先に、その中で期末が近い順。名前ごとに最初に見つかった回の対応を使う。同じ表に同じ土台が 2 行以上ある行（区分ごとの「その他」）は、区分が決まり、区分の中でその土台が 1 行だけなら、（区分、土台）で覚えた対応を同じ順で引いて、見つかればそれを使う（通期と四半期で表の粗さが違い `その他#n` の番号がずれても当たる）。同じ期で 2 行が同じ要素になったら両方とも当てない。印字順は、全回の印字順をまとめた順位と、当てる期と同じ種類（通期・四半期）の回だけをまとめた順位の両方で最長増加部分列を取り、どちらかで残った対応を残す（同じ種類の回が無ければ全回の順位だけ）。通期と四半期で並びが入れ替わる科目（四半期の短信は半期報告書と同じ並びで、通期と違う会社がある）を、全回の順位だけで落とさないため。どちらの順位でも食い違う対応（通期と四半期で `その他#n` がずれた行）は捨てる。当たらない行は PDF の行名のまま印字の位置に残す。値は覚えた向きで XBRL の符号にそろえる。

**益と損で行名が変わる科目**: 利益の期は「営業利益」、損失の期は「営業損失」と印字されるが、XBRL の要素は 1 つ（損失は負の値）の科目。符号付きの利益の要素は、その会社の XBRL のラベル（NFKC 後）が「…利益又は…損失（△）」か「…利益（△損失）」「…利益（△は損失）」の形のもの（日本基準の `OperatingIncome` / `OrdinaryIncome` / `IncomeBeforeIncomeTaxes` / `ProfitLoss` / `ProfitLossAttributableTo…`、IFRS の `OperatingProfitLossIFRS` / `ProfitLossIFRS` など）。要素名は決め打ちにしない。同じ期末に PDF と XBRL がある回で、値で符号付きの利益の要素に付いた行について、その PDF の行名（土台。`#n` 付きは除く）とその要素のラベルの「…利益」の末尾の「利益」を「損失」に替えた名前を、損失の名前として覚える（PDF の「税金等調整前四半期純利益」→「税金等調整前四半期純損失」、ラベルの「親会社株主に帰属する当期純利益」→「親会社株主に帰属する当期純損失」）。1 つの名前が 2 つの対応になればその名前は覚えない。その回で値で付いた名前は損失の名前にしない。PDF だけの期で、覚えた対応（土台でも（区分、土台）でも）が無い行のうち、その表にその土台が 1 行だけで、値が 0 以下のものに、損失の名前で覚えた対応（向きは利益の行で覚えたもの）を同じ順で引く。その期に同じ要素の対応が他の行にあれば当てない（同じ表に「営業利益」と「営業損失」があっても、付いていた「営業利益」を外さない）。損失の名前で当てた行も、印字順の安全弁と区分の決め方（`OperatingIncome` は PL の区分の合計）では値で覚えた対応と同じに扱う（§18 の `matched_by` も `value`）。ラベルが「又は」でも「（△…損失）」でもない別々の要素（「為替差益」と「為替差損」、「持分法による投資利益」と「持分法による投資損失」）と、損失が正の値の別の要素（固定資産除却損・減損損失・投資有価証券評価損）は、利益の要素が符号付きでないので損失の名前を作らない。値が正の「〜損失」の行は、PDF で損失を正で印字したのか分からないので当てない。

**名前での当てはめ（第 2 段）**: PDF が主の列で、値で覚えた対応がどの回にも無い行（土台でも（区分、土台）でも）だけ、行名をその会社・同じ表の XBRL 要素の `label_ja` と比べる。PDF は土台、XBRL は NFKC と「又は…（△）」「（△…）」の除去をしたラベル。PDF の同じ表にその土台が 1 行だけなら、完全一致する要素がちょうど 1 つのときに当てる。一致する要素が無く、行の値が 0 以下なら、符号付きの利益の要素（上の「益と損で行名が変わる科目」）のラベルから作った損失の名前（全 filing を通してその名前になる要素がちょうど 1 つ）とも比べる。上の段で損失の名前として覚えた名前の行は、値で覚えた名前と同じく名前では当てない。それで決まらない行（同じラベルの要素が 2 つ以上: IFRS の流動・非流動の「借入金」、日本基準の「貸倒引当金」、または PDF の同じ表に同じ土台が 2 行以上）は、区分の中で比べる: PDF の行の（区分、土台）と XBRL の要素の（区分、ラベル）が一致し、その会社の XBRL でその区分の中に一致する要素がちょうど 1 つ、PDF 側もその区分の中でその土台が 1 行だけのときに当てる。XBRL の要素の区分も、各 filing の表の並びで後ろで最初に来る合計で決める。区分の中で比べるときは「その他」を当てない（短信 PDF の表が XBRL より粗く、PDF の「その他」が XBRL の複数の要素の和になる会社がある。値で覚えた対応だけで当てる）。その期で値の対応が既に取った要素には当てない。PDF の行と XBRL の要素で単位の種類（金額 / 1 株当たり）が違うときも当てない（KAB-452）。名前で当てた行どうしが同じ要素になったら両方外す。印字順は、値の対応の順位（上の 2 つの順位それぞれ）に XBRL の並びを差し込んだ順位で見て、値の対応をすべて残したうえで食い違う名前の対応を捨てる。符号の向きは分からないので PDF の値をそのまま使う。合計行は PDF（「資産合計」）と XBRL（「資産」）で名前が違うのでこの段では付かない（値で付くか、下の第 4 段で付く）。新しく出た科目で、PDF の期と XBRL の期が別々にしか無い（値で覚える回が無い）ものがこれでつながる。 XBRL のラベルが現金及び現金同等物の期首残高の形の要素は、名前の索引に入れない（IFRS の `CashAndCashEquivalentsIFRS` は、タクソノミの年度によって最初のラベルが「現金及び現金同等物の期首残高」で、PDF の期首残高の行が期末の現金の要素に付く）。

**印字順で落ちた行の区分での確かめ（第 3 段）**: 上の 2 段で対応の候補があったのに印字順の安全弁で落ちた行（値で覚えた対応、名前で 1 つに決まった対応）を、区分の中で確かめ直す（KAB-451）。会社が区分の中の並びを年によって入れ替える（営業外収益の「為替差益」と「持分法による投資利益」）と印字順だけでは落ち、区分をまたいで科目が動く（流動負債に出た「資産除去債務」に、固定負債の要素で覚えた対応がある）と、落ちたあと名前で当てる手が無かったため。区分が決まり、区分の中でその土台が 1 行だけの行に限り、次の順で 1 つ選ぶ。(1) その（区分、土台）で値で覚えた対応（「区分の中の対応」）があり、その要素が、覚えた全回の XBRL の表で同じ区分にだけあるとき、それを当てる（`matched_by` は `value`）。(2) 無ければ、第 2 段の区分の中の名前（PDF の（区分、土台）と XBRL の（区分、ラベル）が一致する要素がちょうど 1 つ）で当てる。値で覚えた要素と同じならその対応（向きも）を、違えばその要素を名前で当てる（`matched_by` は `name`、向きは PDF の値のまま）。単位の種類が違えば当てない。(2) では「その他」を当てない（第 2 段と同じ。「その他」は (1) の区分の中で値で覚えた対応でだけ戻る）。選んだ要素を、その期のほかの行が値の対応か名前の対応の候補として持っていれば当てない（自分が落ちた要素は除く）。この段で当てた行どうしが同じ要素になったら両方外す。この段の行は印字順の安全弁にかけない（区分の中で確かめたことで置き換える）。区分をまたぐ食い違いで、移った先の区分で値の対応も名前の対応も見つからない行は落ちたまま。IFRS の PL（区分の合計の要素が無い）と、区分が決まらない行（合計の行に対応が付かない）はこの段にかからない。

**合計の行の名前での当てはめ（第 4 段）**: 上の 3 段のあとで付いていない合計の行を、行名の末尾の「合計」「計」を落とした名前（語幹）で当てる（KAB-451）。合計の行は値で覚えた対応でしか付かず、値で覚える回が無い期（その連結区分で PDF と XBRL がそろう回が無い: 新規上場、連結と個別の切替え、PDF が 1 期しか読めていない会社。会計基準を切り替えたあとの新しい合計。合計が 0 の行）では区分も決まらず、区分の中の行（「貸倒引当金」）も付かなかったため。対象は、上の 3 段で付かなかった行のうち、その表にその土台が 1 行だけ、値で覚えた名前（土台・（区分、土台）・損失の名前）でない、土台そのものが XBRL のラベルにない行。語幹は土台の末尾の「合計」か「計」を 1 回落としたもので、2 文字以上、「その他」でないもの（「合計」「計」「小計」だけの行と、「累計」「会計」「設計」「統計」のような 2 文字の語は語幹にならない。「累計額」は「計」で終わらない）。語幹が、その会社・同じ表の XBRL のラベル（第 2 段と同じ正規化）のちょうど 1 つの要素と一致し、その要素がその期にまだ使われておらず、単位の種類が合うときに候補にする（`matched_by` は `name`、向きは PDF の値のまま）。要素の種類（区分の合計かどうか）は問わない（「法人税等合計」→ `IncomeTaxes`、「販売費及び一般管理費合計」→ `SellingGeneralAndAdministrativeExpenses`、「利益剰余金合計」→ `RetainedEarnings` も付く）。日本基準の標準のラベルは「資産」「負債純資産」「純資産」で、「資産合計」と「負債純資産合計」の語幹は別の要素になる。ラベルが「負債純資産合計」の会社では、その行は第 2 段で完全一致で付き、「資産合計」だけがこの段に来る。候補どうしが同じ要素になれば両方外す。日本基準と IFRS の合計が混ざらないよう、その表で付いた合計の要素（区分の合計・大区分の合計・`LiabilitiesAndNetAssets` / `LiabilitiesAndEquityIFRS`）と候補の合計の要素を `…IFRS` かどうかで数え、多い方の基準の候補だけ残す（同数ならどちらの候補も当てない。連結の IFRS の表を個別として読んだ PDF の「資産合計」を、個別の日本基準の `Assets` にしない）。残った候補を合計の行の対応として置き、区分を決め直して上の 3 段をもう一度通す。そのうえで、最初の結果で付いていなかった行にだけ、2 回目の結果を足す（最初の結果で付いていた行は変えない。最初の結果でほかの行が使った要素は足さない。足す行どうしが同じ要素なら両方外す）。合計の行は印字順の安全弁にかけない。2 回目で区分が決まって付く行（区分の中の「貸倒引当金」「その他」以外の同名の行、区分の中で値で覚えた対応）もこれで付く。CF の「現金及び現金同等物の期末残高」は、会社の XBRL にある期末の残高の要素のラベルと語幹（「現金及び現金同等物」）が合う例が無かったので入れない。

**同じ期末に複数の出どころ**: 表ごとに、その表がある出どころのうち 有報 > 短信 XBRL > 短信 PDF を主にし、同じ出どころは提出が新しい方。短信 PDF は、表ごとに、行数がその期の短信 PDF のうちいちばん多いものの 7 割以上ある開示のうち、提出が新しい方を土台にする（出し直しの短信は全部の行があるので土台になる。一部訂正のお知らせは訂正した所しか載らず、土台にすると行が減る）。土台より後に出た開示（一部訂正のお知らせ）は、土台にある行の値だけを置き換える（新しい開示が先。対応が付いた行は要素で、付かない行は行名で突き合わせる。「その他」と `#n` の付く行名は置き換えない）。後の開示にしか無い行は、要素に対応が付いた行だけ足す（一部訂正のお知らせにだけ包括利益計算書が載る開示がある）。付かない行は出さない（訂正前・訂正後を並べた開示の「(中略)資産合計」などの読み取りの余り）。PDF と XBRL の対応を覚える組（上の「PDF の行と要素の対応」）の PDF は、今までどおり提出が新しい方（訂正後の値が XBRL と合う。土台に替えると、本番で覚える対応が大きく入れ替わった）。主に無い要素は下位の出どころで補う。PDF は対応が付いた行だけ補い、付かない行は主が XBRL なら出さない。列の出どころ（TSV の最後の行 `出どころ`）は主。セルごとに、その値を採った出どころも持つ（主と違えば補ったセル）。

**行の並びと名前**: いちばん新しい期の並びを土台に、古い期にだけある行はその期の直前の行の後ろに差し込む。行名は、PDF との対応があれば PDF の印字名（`#n` と、区分見出しがくっついたもの（`有形固定資産建物…`）は要素のラベルに戻す）、無ければ要素の `label_ja`（「又は…損失（△）」「（△は増加）」を落とす）。

**区分と見出し**: 行の区分と見出しは、書類の表示リンク（§4.4）から決める。行の並びや行名の文字列からは決めない。1 つの行は、大区分・区分（粗い区分。行をまとめる判定と、宣言の無い書類の見出しに使う）と、見出しの道筋（外側から。画面・API・TSV に出す）を持つ。BS・PL・CF とも見出しを出す。

- **見出し（宣言どおり）**（`_tree_heads`）: 表示リンクの区分の要素すべてに深さ（§4.4）があれば、値の要素ごとに、祖先の見出しの要素（名前に `Abstract` を含む）を全部、外側から並べたものを見出しにする。段の数は決めない（資産の部 > 固定資産 > 有形固定資産 の 3 段、鉄道・電力・リースなど業種の見出し、会社独自の見出し、利益剰余金 > その他利益剰余金 の小見出しもそのまま）。見出しの名前は §4.4 の表示名（無ければ要素名）。金額のある要素の下に子が並ぶ形（銀行の 有形固定資産 > 建物、電力の 固定資産 > 電気事業固定資産 > 水力発電設備）は、親の行は行のまま、子を親の数だけ字下げする（`indent`）。次の 4 つは宣言どおりにしない。印字（本表の HTML）と比べて決めた
  - **合計の後ろの見出し**: 見出しの中で、その見出しの合計の要素（見出しの名前から `Abstract` を除いた要素。`PropertyPlantAndEquipmentAbstract` → `PropertyPlantAndEquipment`）より後ろに来る、子のある見出しは出さない（中の要素は外の見出しの下）。TDnet の書類で、会社が足した科目がタクソノミの雛形の見出しごと合計の後ろに並ぶ（6094 の「建物及び暖房、照明、通風等の附属設備」「引当金」「その他の負債で１年内に…」、4052 の「その他の投資その他の資産」など）。印字にこの見出しは無く、科目は合計の前に並ぶ。子の無い見出しは次の決まり
  - **子の無い見出し**: 見出しだけ置き、科目を見出しの兄弟に並べる書類（有形固定資産の見出しの後ろに土地・その他（純額）・有形固定資産合計を兄弟で並べる）。同じ段で後ろに続く要素を、その見出しの合計の要素まで（次の見出しが来るか、親が閉じるまで）その見出しの下にする
  - **小計の子**: 名前が `Subtotal` で始まる要素（CF の小計）の子は字下げしない。TDnet の書類で、小計の前に印字する科目が小計の子として後ろに並ぶ
  - **区分の外の要素**（`OUTSIDE_SECTIONS`）: BS の新株予約権・非支配株主持分（IFRS も）、CF の換算差額・現金及び現金同等物の増減額・期首/期末残高・名前が `CCE` で終わる要素は、`HEAD_SECTIONS` の見出し（株主資本・その他の包括利益累計額、営業・投資・財務 など）の中にあれば、その見出しの外に出す。その他の包括利益累計額の見出しの中、合計の後ろに新株予約権を置く書類がある。印字では区分の外
- **大区分・区分**（`_flat_parts`）: 並びを頭から読み、見出しの要素のうち下の表にあるものが出たらその区分を開く。あとに続く要素はその区分。区分は、親でない次の見出しか、親の区分・大区分の合計で閉じる。区分の合計の要素でも閉じる（合計の行はその区分）が、BS の流動資産・固定資産・流動負債・固定負債（IFRS の同じ区分も）と、CF の営業・投資は、並びの後ろにまだ見出しがある間は合計では閉じない（`OPEN_AFTER_TOTAL`）。表示リンクの並びで合計の後ろに科目や小見出しが続く書類があるため（本番で 4052・6094 など。「引当金」などの小見出しの中の科目が流動負債合計の後ろに並ぶ）。この区分の合計の後ろは次の見出しか大区分の合計なので、閉じなくても別の科目を拾わない。有形・無形・投資その他は合計で閉じる（合計の後ろに、固定資産の直下の科目が見出し無しで続く会社がある）。純資産の区分（株主資本・その他の包括利益累計額・親会社の所有者に帰属する持分）と CF の財務・PL の区分は、合計の後ろに区分の外の科目（新株予約権・非支配株主持分、換算差額、経常利益）が続くので合計で閉じる。有形固定資産・無形固定資産・投資その他の資産（固定資産の見出しの中）と繰延資産の合計の要素（投資その他の資産は `InvestmentsAndOtherAssetsGross` も）が見出し無しで出たら（四半期の BS は内訳を出さず 1 行にする）、その 1 行をその区分にする（`LONE_SECTIONS`）。流動資産・固定資産・流動負債・固定負債（IFRS の同じ区分も）の合計の要素が見出し無しで出たときも同じ（固定負債を 1 行で出す会社）。表に無い見出し（鉄道・電力・リースなどの業種の見出し、会社独自の見出し、`RetainedEarningsAbstract` など区分の中の小見出し）は飛ばし、その中の要素は外側の区分のまま。並びだけを見るので、段は表の親子（有形・無形・投資その他 の親は固定資産）で組む
  - BS の大区分: `AssetsAbstract` / `LiabilitiesAbstract` / `NetAssetsAbstract`（IFRS は `AssetsIFRSAbstract` / `LiabilitiesIFRSAbstract` / `EquityIFRSAbstract`）。大区分は合計の要素（`Assets` など）で閉じる。大区分の合計の行、純資産の区分の合計の後ろの行（新株予約権・非支配株主持分）は区分なしで大区分だけ。`LiabilitiesAndNetAssets` / `LiabilitiesAndEquityIFRS` は大区分の外。大区分の見出しが無く区分の見出しだけある書類（負債の見出しを置かない IFRS）は、区分が属する大区分にする。大区分の見出しが無いまま大区分の合計が出たら（電力の負債・純資産）、前の大区分の後ろからその合計までをその大区分にする
  - BS の区分（見出し → 区分の合計の要素）: `CurrentAssetsAbstract` → `CurrentAssets`、`NoncurrentAssetsAbstract` → `NoncurrentAssets`、その中の `PropertyPlantAndEquipmentAbstract` / `IntangibleAssetsAbstract` / `InvestmentsAndOtherAssetsAbstract`、`DeferredAssetsAbstract`、`CurrentLiabilitiesAbstract`、`NoncurrentLiabilitiesAbstract`、`ReservesUnderTheSpecialLawsAbstract1`（特別法上の準備金）、`ShareholdersEquityAbstract`、`ValuationAndTranslationAdjustmentsAbstract`。IFRS は `CurrentAssetsIFRSAbstract` → `CurrentAssetsIFRS`、`NonCurrentAssetsIFRSAbstract` → `NonCurrentAssetsIFRS`、`CurrentLiabilitiesIFRSAbstract` → `TotalCurrentLiabilitiesIFRS`、`NonCurrentLiabilitiesIFRSAbstract` → `NonCurrentLabilitiesIFRS`、`EquityAttributableToOwnersOfParentIFRSAbstract` → `EquityAttributableToOwnersOfParentIFRS`。固定資産の直下の行と固定資産合計の行は区分「固定資産」。銀行・保険などは大区分だけになる。IFRS の資本は、親会社の所有者に帰属する持分の見出しを置かない書類が多く、そのときは大区分「資本」だけ
  - CF: `NetCashProvidedByUsedIn(Operating|Investment|Financing)ActivitiesAbstract`（IFRS は `CashFlowsFrom(Operating|Investing|Financing)ActivitiesIFRSAbstract`）で 営業・投資・財務 の 3 区分。財務 CF の合計より後ろ（換算差額・増減額・期末残高）は区分なし
  - PL: `CostOfSalesAbstract` / `SellingGeneralAndAdministrativeExpensesAbstract` / `NonOperatingIncomeAbstract` / `NonOperatingExpensesAbstract` / `ExtraordinaryIncomeAbstract` / `ExtraordinaryLossAbstract`（日本基準。IFRS の PL はこれらの見出しが無いので区分なし）
- **大区分・区分を深さにかかわらず並びから決める理由**: 行をまとめる判定（下の「同じ区分」）は KAB-481 の粗い区分で組んである。見出しを宣言どおりの自由な段にすると、同じ科目でも書類によって段が違い（雛形の見出しの有無、会社独自の見出し）、まとまっていた行がまとまらなくなる。深さで区分を決め直すと、深さのある書類と無い書類（埋め直しの前後）で同じ行の区分が変わり、まとまる組が変わる。並びの決まりは深さを見ないので、埋め直しの前後で判定が変わらない。深さのある書類の見出しは表示だけに使う
- **並びが印字と合わない書類**: 表示リンクを順（`order`）で並べると、区分の合計の後ろに、その区分の小見出しと科目が来る書類がある（本番で 4052・6094 など。数は少ない）。深さのある書類の見出しは親子で決まるので印字どおり（6094 の建物・工具器具備品は有形固定資産、顧客関連資産は無形固定資産、4052 の敷金及び保証金は投資その他の資産、6094 の財務 CF の合計の後ろのリース債務の返済などは財務 CF）。大区分・区分（並びの決まり）は今までどおりで、有形・無形・投資その他の合計の後ろに来た科目は「固定資産」、CF の財務の合計の後ろに来た科目は区分なし（まとめる判定にだけ使う）。深さの無い書類は §4.5 の `--redo-flat` で読み直せば宣言どおりの見出しになる
- **行の区分**（`row_parts`）: その行に値があるいちばん新しい列の、その値を出した書類の区分（大区分・区分と見出しの組）。会社が科目を別の区分へ移したら（流動 → 固定）、新しい書類に従う。短信 PDF から対応で入った値は区分を持たないので、PDF だけの期の行は、同じ要素に値がある XBRL の列の区分を借りる
- **表示リンクの区分が無い行**: 対応の付かない PDF の行（`pdf:`）と、表示リンクの無い書類（`presentation_status` が `ok` でない）だけに値がある行。上で最初に区分のある行と同じ区分にする。その行が区分か大区分の合計の行なら、下で最初に区分のある行と同じ。上にも下にも無ければ区分なし。表のどの行にも表示リンクの区分が無ければ（表示リンクの無い書類だけの会社、PDF だけの会社）、表全体が区分なし
- **会計基準が混ざる表**: 表の基準は、区分のあるいちばん新しい列の基準。宣言の無い書類の行で、違う基準の行は粗い区分（資産・負債・純資産/資本 と、流動資産・固定/非流動資産・流動負債・固定/非流動負債、CF の 営業・投資・財務）で表の基準の区分に寄せて出す（IFRS の表では、日本基準の有形固定資産の行は「非流動資産」、株主資本の行は大区分「資本」だけ）。粗い区分に当たらない区分は大区分だけ
- **見出しの名前**（宣言の無い書類の 2 段）: 区分の合計の要素の行名から末尾の「合計」「計」を落としたもの（PDF の印字名があればそれ。「その他の包括利益累計額」と「評価・換算差額等」はそのまま分かれる）。合計の要素がその会社に無ければ決まった名前。大区分は、行名が「合計」「計」で終わらなければ決まった名前（IFRS の資本合計の行名が持分変動計算書の「四半期末残高」になっている会社がある）
- **宣言のある書類と無い書類が混ざる表**: 行の見出しは、その行に値があるいちばん新しい書類のもの（上の「行の区分」）。いちばん新しい書類に深さが無い行（古い期にだけある行など）は、表の中に同じ大区分・区分の合計の要素の行（区分なら `CurrentAssets` などの行、大区分だけなら `Assets` などの行）があって、その行の見出しが宣言どおりなら、その見出しを借りる（字下げは 0）。借りる先が無ければ 大区分・区分 の 2 段（PL は出さない。KAB-481 のまま）。埋め直し（§4.5）の前は、新しい書類だけ宣言どおりで、古い期にだけある行が 2 段の名前（資産 > 固定資産）で並ぶことがある。会計基準を切り替えた会社の古い基準の行（宣言の無い書類）は、上の「会計基準が混ざる表」で寄せてから借りる。宣言のある古い基準の書類の行は、その書類の見出しのまま出す（寄せない）

**タグが替わって割れた行をまとめる**: 行のキーは要素名なので、会社が期の途中でタグを替えると（`NotesAndAccountsReceivableTrade` → `AccountsReceivableTrade`、`ext:` から標準へ、`ext:` の名前替え、日本基準から IFRS へ）、同じ科目が 2 行に割れる。表を組み立てるとき（上の列を作り、連結と個別をつないだ後、表示する期で切る前）に、次を全部満たす行どうしを 1 行にまとめる。(1) 区分が同じ（下の「同じ区分」）。(2) 行名（画面に出る名前）が同じ。(3) 値の入る期が 1 つも重ならない。(4) 単位の種類（金額 / 1 株当たり）が同じ。(5) どちらも XBRL の要素の行（標準か `ext:`。`pdf:` の行はまとめる対象にも相手にもしない）。期が重ならないかは、表示する期で切る前の全部の列で見る（表示する期の数で行が変わらないため）。同じ（区分, 行名, 単位）の行が 3 つ以上あるときは、期が重ならない組をつないだかたまりの中で、どの 2 行も重ならないときだけかたまりを 1 行にし、重なる組が 1 つでもあればそのかたまりは 1 つもまとめない。まとめた行のキー・位置・行名・単位・区分は、いちばん新しい期に値がある行のもの。セルは期ごとに値のある方（重ならないので衝突しない。セルの出どころもそのまま）。まとめられた行のキーは API の行の `merged_keys`（新しい期の行から順。まとめていない行は空）に残す。画面と TSV には出さない。
- **表示のときにまとめる理由**: DB（`xbrl_facts` / `xbrl_pdf_matches` / `xbrl_statement_values`）と PDF の対応付けは要素名のまま残す。付け替えは会社の書類の事実で、取込や対応付けで書き換えると原文と突き合わせられなくなる。まとめは対応付けの後ろで、表の見せ方だけを変える。対の一覧も持たない（規則で決まる）
- **まとめない行名**: 末尾の「(純額)」を外して「その他」、「減価償却累計額」「減損損失累計額」で始まるもの、「貸倒引当金」。同じ区分の中に同じ名前で何行も出る科目で、期によって別の資産の行（建物の減価償却累計額と機械の減価償却累計額など）がたまたま重ならないことがあるため。「その他有価証券評価差額金」「その他の固定負債」「その他の包括利益」のような「その他」で始まるだけの名前はまとめる
- **同じ区分**: 行の大区分・区分（上の **区分と見出し** の「大区分・区分」と「行の区分」。PL も含む）が同じこと。見出し（宣言どおりの道筋と字下げ）は見ない。PL は営業外と特別に同じ名前の科目（投資有価証券売却益、助成金収入など）があり、区分を見ないと出る期が違うだけで 1 行になってしまう。要素名の末尾（`NOI` / `EI`）では見ない（会社独自の要素には付かない）。会計基準が違う行どうし（片方が日本基準の区分、片方が IFRS の区分）は、区分の名前が合わないので粗い区分（上の「会計基準が混ざる表」と同じ）が同じならまとめる。PL と、BS の純資産/資本の中は粗い区分が無いので、基準が違えば同じ区分として扱う（日本基準の営業外収益の行と IFRS の区分なしの行、株主資本の資本金と IFRS の資本金）。流動と固定/非流動は基準をまたいでも分ける（日本基準で流動資産にあった繰延税金資産と IFRS の非流動資産の繰延税金資産はまとめない）
- **除外の一覧**: 規則ではまとまるが別の科目だと分かった組は `config/xbrl/row_merge_exclusions.json` の `pairs` に、会社（5 桁コード）・表・要素 2 つで書く。一覧の組は期が重なる組と同じに扱う（3 行以上のかたまりにあれば、そのかたまりはまとめない）
- **会計基準の切替え**: 日本基準 → IFRS で割れた行は、上の「同じ区分」の粗い区分でまとめる（「のれん」の `Goodwill` と `GoodwillIFRS` など）。そのかわり、表示する列の会計基準（`periods[].accounting_standard`）が 2 つ以上ある表は、列の見出しに会計基準を出す（画面は `docs/screen/stock.md` の「業績詳細」、TSV は列ごとの `会計基準` の行）。基準が 1 つの表の見た目は変えない
- **まとめないもの**: `pdf:` の行を XBRL の行に寄せること、行名が少し違う表記揺れ（「短期貸付金」と「短期貸付金（純額）」）、同じ行名で期が重なり値も同じ 2 行

**限界**: CF の期首残高は XBRL に当期の fact が無い（有報は前期末の fact を残さない）。XBRL の期に前期末の期末残高から作って出すことはせず、PDF の期首残高の行（「現金及び現金同等物（の）期首残高」「期首現金及び現金同等物残高」と、頭のローマ数字・末尾の「（…計上額）」・「会計方針の変更を反映した」が付いたもの）は、どの要素にも当たらなければ表にも §18 の対応にも出さない（期首残高の値は前の年度末の期末残高と同じで、情報は欠けない。`xbrl_facts` には残す）。会社独自の要素に当たった行は出す。「新規連結子会社の」などほかの語が付いた行は増減の科目なので外さない。四半期に新しく出た科目・0 の行・前の回と値も名前も違う行は当たらない。米国基準は PDF を読んでいないので XBRL の期だけ。表示リンクの定義の無い書類の XBRL の振り分けは要素名と並びの規則なので、規則に当たらない会社独自の並びでは BS / PL / CF の境がずれることがある。名前での当てはめは、会社が期をまたいで同じ行名を別の科目に使い回すと誤る（同じ表の XBRL に同じラベルの要素が 1 つだけ（区分の中で 1 つだけ）で、値の対応が無いときに限る）。区分見出しを外す一覧に無い見出し（売上原価・販管費の内訳の見出しなど）が付いた行名は外さない。2 回目の印字は PL だけで、XBRL のある回で一度も確かめられていない名前は写しにしない。損失の名前は 2 回目の印字にならないので、損失の期に 2 回印字される「当期損失」（IFRS）は 2 行とも当たらない。区分の合計を会社独自の要素（`ext:`）で出す会社は、その区分が付かず前後の区分に入る。

## 18. 出どころをまたいで要素名で引く（`xbrl_pdf_matches` と `xbrl_statement_values`）

§17 の対応（PDF の行 → XBRL の要素）を表に残し、有報・短信 XBRL・短信 PDF を同じ要素名で SQL から引く。画面・API は使わない（集計用）。

**対応の表 `xbrl_pdf_matches`**（列は [`schema/xbrl.md`](../schema/xbrl.md)）: 対象は PDF だけの期の開示。同じ銘柄・同じ期末・同じ連結区分に、本表の付いた `edinet` / `tdnet` の `parsed` の filing が無い `tdnet_pdf` の filing（§17 の材料と同じ `doc_type`）。その本表（`pdf-bs` / `pdf-pl` / `pdf-cf`）の当期 fact ごとに 1 行（CF の期首残高でどの要素にも当たらない行は入れない。§17 の限界）。同じ期末に XBRL がある期の PDF は入れない。突き合わせは `three_statements.pdf_only_matches`（§17 の `load_both` → `learn_pair` → `apply_learned` を、PDF が主の列と同じ引数で呼ぶ。別の実装は持たない）。`element_key` は §17 の行のキー（標準は `local_name`、会社独自は `ext:<local_name>`）。会社独自の要素は有報（`jpcrp…_E…`）と短信 XBRL（`tse-…-証券コード`）で `xbrl_elements` の行が分かれるので、`element_id` ではなく文字列で持つ。付かない行は `element_key` と `matched_by` が NULL。同じ期末の PDF が 2 つ（訂正）なら両方に付ける（ビューが新しい方を採る）。PL の 2 回目の印字（§17）は前の行と同じ `element_key` で入る（ビューは同じ filing の fact の id の小さい方を採るので 1 つ）。対象の銘柄は銘柄マスタ `stocks` にある銘柄だけ（無い会社には突き合わせる有報も短信 XBRL も無い）。`xbrl_facts` の PDF の fact は消さない。

**付け直し**: `tools/tdnet_pdf/matches.py`（全銘柄か `--code`）。銘柄ごとに消して入れ直して commit するので何度流しても同じ。`--batch N` は N 銘柄ごとに `pdf_matches batch done=… codes=… filings=… matched=… unmatched=… failed=… left=…` を 1 行出し、`database is locked` の銘柄は区切りの終わりに 5 秒待って 1 回やり直す（§16.5 の `financials.py` と同じ）。最後の行は `pdf_matches codes=… filings=… matched=… unmatched=… failed=… skipped=… deleted=…`（`skipped` は `stocks` に無くて対象にしなかった銘柄の数、`deleted` は全銘柄で流したときに消した、`stocks` に無い銘柄の既存の行数。`--batch` の行も `failed` と `left` の間に同じ 2 項目が入る）。`--code` で `stocks` に無い銘柄を指定すると `pdf_matches skipped code=… reason=not in stocks` を出して飛ばす（既存の行は触らない）。全銘柄で流すと、`stocks` に無い銘柄の既存の行は銘柄ごとに DELETE して commit する（`database is locked` は他の銘柄と同じく区切りで待ってやり直す）。`tools/tdnet_pdf/financials.py` は最後に、その実行で `parsed` / `retired` / `failed` になった開示の銘柄だけ同じ処理を流す（`--no-matches` で流さない。失敗しても読み直しの結果は残り、終了コードが 1）。cron には入れない。付け直すのは次のとき:

- PDF を読み直したとき（`financials.py` が続けて付ける）
- 突き合わせの規則（`three_statements.py`）を直したとき（全銘柄）
- 直近の PDF だけの期の次に、有報・短信 XBRL が入ったとき（値の対応を覚える回が増える。四半期に 1 回、全銘柄で足りる）

PDF の fact は読み直しのたびに消して入れ直す（id が変わる）。`delete_filing_payload`（`store.py`）が fact より先にその filing の対応を消すので、古い対応が別の fact を指して残らない。

**ビュー `xbrl_statement_values`**: 1 行が 1 つの値。列は `code` / `period_end`（`xbrl_filings.fiscal_year_end`）/ `period_type`（Q1/Q2/Q3/FY）/ `is_consolidated`（1=連結）/ `context_type`（instant/duration）/ `period_start`（duration の期首）/ `element_key` / `value` / `unit` / `source`（edinet/tdnet/tdnet_pdf）/ `filing_id` / `submitted_at` / `fact_id` / `matched_by`（PDF の行だけ value/name）。日本語のラベルは持たない（`xbrl_elements.label_ja` を引く）。

- XBRL の行: `edinet` / `tdnet` の §17 の材料の filing。当期の fact、次元が連結/単体のメンバーだけ、単位が円・円/株・無し、`jppfs_cor` / `jpigp_cor` と会社独自（`is_custom` で `jpdei_cor` / `jpcrp_cor` / `tse-ed-t` / `tse-re-t` / `pdf-*` 以外）。DEI が「連結財務諸表を作っていない」の filing は全部個別。Q2 / Q3 の duration は期首からの累計だけ（期間が 120 日 / 210 日以上。3 か月だけの値は出さない）
- PDF の行: `xbrl_pdf_matches` で要素の付いた行だけ。値は `value_num * sign`。付かない行は出ない
- 同じ `code` / `period_end` / `is_consolidated` / `element_key` は 1 行: 有報 > 短信 XBRL > 短信 PDF、同じ出どころは提出が新しい方（訂正を含む）、同じ filing なら fact の id の小さい方。上位の書類に無い要素は下位から出る（§17 の補いと同じ）
- 速さ: 1 つ選ぶのは `row_number() OVER (PARTITION BY code, period_end, is_consolidated, element_key …)`。外の WHERE が `code` か `element_key`（PARTITION BY の列）だけなら SQLite は中の 3 つの SELECT まで押し込み、`element_key` は `idx_xbrl_elements_local` / `idx_xbrl_elements_ext_key` / `idx_xbrl_pdf_matches_key`、`code` は `idx_xbrl_filings_code_submitted` / `idx_xbrl_pdf_matches_filing` で引く。`period_end` / `is_consolidated` も押し込める。`value` や `source` だけの絞り込みは全件を読む。標準要素の行は `+e.namespace_key`（索引に使わせない）で、銘柄で引くときに名前空間の索引で全要素を読むのを止めている
- §17 と違うところ: 表（BS / PL / CF）を振り分けないので、注記・株主資本等変動計算書の要素も出る。PDF の期に XBRL もある回（PDF が補うセル）は出ない。API は同じ要素の当期 fact が 2 つあると id の小さい方を採るが、ビューは累計を採る（四半期報告書の 3 か月の値。36790 の `ProfitLossBeforeTaxIFRS`）。`fiscal_year_end` が同じでも決算期を変えた回の期間の長さは見ない

```sql
-- 前受金の全社・全期間（連結）
SELECT code, period_end, period_type, value, source
FROM xbrl_statement_values
WHERE element_key = 'AdvancesReceived' AND is_consolidated = 1
ORDER BY code, period_end;

-- 1 銘柄の全要素
SELECT period_end, element_key, value, source FROM xbrl_statement_values
WHERE code = '98800' AND is_consolidated = 1 ORDER BY element_key, period_end;

-- 付かなかった行の多い順（tools/tdnet_pdf/matches.py --unmatched 50 と同じ）
SELECT m.statement, e.local_name, count(*) AS n
FROM xbrl_pdf_matches m JOIN xbrl_facts f ON f.id = m.fact_id JOIN xbrl_elements e ON e.id = f.element_id
WHERE m.element_key IS NULL GROUP BY 1, 2 ORDER BY n DESC LIMIT 50;
```

**限界**: §17 の限界（四半期に新しく出た科目・0 の行・名前の使い回し）はそのまま。PDF だけの期の CF の期首残高は XBRL に当期の要素が無いので付かず、§17 のとおり対応の表に入れない。ビューの定義を変えると `init_db` が作り直す（定義の文字列が違えば DROP して CREATE）。
