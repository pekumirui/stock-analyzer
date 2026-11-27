# 取込と保存の規則: XBRL

決算短信 XBRL、EDINET 有報・半期報・四半期報 XBRL、および過去の決算短信 PDF からの財務数値抽出・正規化・原文保存・財務 3 表統合の仕様を定義する正本である。システム全体は [全体像](../OVERVIEW.md)、実装時の共通規約は [AGENTS.md](../../AGENTS.md) を参照する。

不明点がある場合に推測で仕様を変えない。特に勘定科目のマッピング定義は会計・業務知識に依存するため、「初期定義（最小セット）」に無い科目を独断で追加してはならない。

ジョブの起動時刻・引数・読み書きするテーブルは自動生成の [ジョブ設計](gen/xbrl.md)、テーブル・列・索引の定義は自動生成の [スキーマ](../schema/xbrl.md)、状態列の遷移図は [状態の遷移](../schema/states.md)、検証 DB による突合結果は [XBRL 検証 DB とゴールデン一致率](../XBRL_VERIFY.md) を参照する。EDINET 側の提出者・書類一覧・ZIP 取得は [EDINET・大量保有](edinet.md)、銘柄画面の表示と API 応答形式は [銘柄](../screen/stock.md) に記載する。

## 全体

### 決めたこと

以下の基本方針を変更するときは、この節と本文の該当箇所の両方を更新する。

1. 取得・パースの対象は TDnet の決算短信 iXBRL（`source='tdnet'`）と EDINET の有価証券報告書・半期報告書・四半期報告書（KAB-73、`source='edinet'`）とし、パーサ・正規化ロジック・マッピング定義を共通で使う。EDINET 側の取得・対象・fact 絞り込みは [EDINET・大量保有](edinet.md)「有報・半期報の財務 XBRL」を参照する。
2. XBRL 層の企業キーは 5 桁の `stocks.code` だけを使う。`stocks.edinet_code`（`NULL` 許容）は KAB-72 で追加済みだが、XBRL テーブルの結合キーには使わない。詳細は [EDINET・大量保有](edinet.md)「提出者」を参照する。
3. コンテキストから連結・単体の別が判定できない場合は連結（`is_consolidated=1`）として扱う。
4. 生データ層と正規化層は既存の SQLite（`DATABASE_URL`、本番は `stock.db`）に同居させる。別 DB ファイルへの分離は将来行数増加が問題になった時点で検討する。
5. 決算速報の画面と API が参照する正本は `statements` テーブルである。速報性を高めるため、TDnet の解析済み短信 XBRL から開示番号単位で同じ形の行を生成して `statements` に入れる（KAB-383、「statements との関係」を参照）。例外として、決算表の修正履歴（KAB-57）だけは `?rev_source=xbrl` または `EARNINGS_PARAMS["revision_source"]` の指定で `xbrl_financials`（`source='tdnet'` の filing のみ）を直接参照でき、既定の `'jquants'` を含めいずれの場合も `statements` は書き換えない。

### 目的とスコープ

#### 目的

本仕組みの目的は以下の 3 点である。

- `data/xbrl` に蓄積している TDnet XBRL ZIP をパースし、数値および短文の fact を欠損なく生データ層へ保存する。
- 企業横断で比較できる標準勘定科目へ正規化し、正規化層（`xbrl_financials`）を構築する。
- 速報の正本は `statements` のままにし、短信 XBRL からは同じ `disclosure_no` の行を足すか、値がある数値列だけを上書きする。J-Quants の行をまとめて消すことと、突合で削除することはしない（「statements との関係」）。BS・PL・CF 本表や注記は `xbrl_financials` 側の基盤にする。

#### スコープ内

本設計の対象範囲を下表に示す。

| 項目 | 内容 |
|---|---|
| 取得元 | TDnet の適時開示 ZIP（`jobs/xbrl/tdnet_xbrl_r2.py`）および EDINET 書類 ZIP（`jobs/edinet/edinet_xbrl.py`）を対象とする |
| 入力ファイル | `data/xbrl/{code4}/` 配下の ZIP と `manifest/{YYYY-MM-DD}.jsonl` を読む。本番データの参照には MCP の `fs_*`（`root=data`）またはマウント先の `/mnt/vps/data` を使う |
| 対象書類 | 決算短信・四半期決算短信・中間決算短信・訂正決算短信のサマリおよび添付本表 iXBRL、ならびに業績予想修正・配当予想修正の iXBRL を対象とする |
| 対象データ | iXBRL 内の全 fact を対象とする。`XBRL_TEXT_MAX_CHARS`（既定 `1000` 文字）を超える長文テキストブロックは値だけを捨て、要素定義（`xbrl_elements`）は残す |
| 正規化対象 | BS・PL・CF の標準科目に加え、EPS・配当・業績予想（KAB-47）、有報の経営指標・従業員・株式数・研究開発費・設備投資額（KAB-78）、報告セグメント（KAB-77、`xbrl_segments`）を対象とする |
| 企業キー | 5 桁の `stocks.code` とし、ZIP 側の 4 桁コードには末尾 `0` を補って照合する |
| 保存先 DB | 既存の `DATABASE_URL` をそのまま使う |

#### スコープ外

以下の事項は本設計のスコープ外とする。

- `AuditDoc/` 配下の監査報告書は、ファイルの走査（`find_ixbrl_files`）では拾わない。ただし manifest を 1 つだけ拾う `find_manifest` が `AuditDoc/` の manifest を先に拾うと、そこに載った監査報告書の iXBRL も和に入る（下の「解析対象の iXBRL ファイル」の 2）。
- セグメント軸付き fact は `xbrl_financials` には入れない（KAB-77 で別テーブル `xbrl_segments` に格納している。「セグメント注記」を参照）。
- `statements` 全体の全面置換や自動突合による既存 J-Quants 行の削除は行わない。
- インフラファンド専用の勘定科目体系は持たない。

### アーキテクチャ

TDnet 短信 ZIP の取得から生データ保存・正規化・`statements` 反映までの処理フローを下図に示す。パース処理が参照する保存先は `data/xbrl` であり、残置の FUSE マウント（`/home/stock/r2-share`）はジョブの出力先に使わない。

```
[TDnet inbs ZIP]
        │
        │ (1) fetch       jobs/xbrl/tdnet_xbrl_r2.py
        ▼
[data/xbrl/{code4}/ + manifest/*.jsonl]
        │
        │ (2) parse       jobs/xbrl/xbrl_parse.py
        ▼
┌──────────────────────────────────────────┐
│ 生データ層・原文層                        │
│  xbrl_filings / xbrl_contexts            │
│  xbrl_elements / xbrl_facts              │
│  xbrl_presentation_roles / xbrl_documents│
└──────────────────────────────────────────┘
        │
        │ (3) normalize   jobs/xbrl/xbrl_normalize.py
        ▼
┌──────────────────────────────────────────┐
│ 正規化層                                  │
│  xbrl_financials / xbrl_segments         │
│  xbrl_shareholder_filings ほか大株主 2 表 │
└──────────────────────────────────────────┘
        │
        │ (4) statements  jobs/xbrl/xbrl_statements.py
        ▼
[statements] ◀── 既存: J-Quants /v2/fins/summary（数値は XBRL 側を優先）
```

定常バッチを構成する 4 つのジョブはそれぞれ単独で再実行でき、cron では `(1)` → `(2)` → `(3)` → `(4)` を 1 行で直列実行する（「cron と CLI」を参照）。既存の `statements` や月次の `*_elements` と名前が衝突しないよう、テーブル名には原則として `xbrl_` 接頭辞を付ける。

| 順序 | ジョブ | 実装スクリプト | 責務 |
|---|---|---|---|
| `(1)` | fetch | `jobs/xbrl/tdnet_xbrl_r2.py` | TDnet 開示一覧から ZIP を取得して既定の `data/xbrl` に保存し、`manifest/{YYYY-MM-DD}.jsonl` に追記する（重複スキップ正本は `manifest` の `xbrl_id`） |
| `(2)` | parse | `jobs/xbrl/xbrl_parse.py` | ZIP を `xbrlp` で解析して生データ層・表示リンク・原文 HTML を保存する |
| `(3)` | normalize | `jobs/xbrl/xbrl_normalize.py` | 生 fact と YAML マッピングから `xbrl_financials`・`xbrl_segments`・大株主テーブルを生成する |
| `(4)` | statements | `jobs/xbrl/xbrl_statements.py` | 解析済みの短信・予想修正 XBRL から `statements` 行を生成・マージする |

### ほかの取込との関係

関連する機能・テーブル・ビューとの連携は以下のとおりである。

- 決算一覧 API が参照する売上総利益は、カバリングインデックス `idx_xbrl_financials_gp_cover` と `idx_xbrl_filings_id_cover` だけで解決し、テーブル本体を読まない（KAB-295）。
- 有報・短信 XBRL の表示リンク（`*pre.xml`）のうち財務 3 表に当たる区分と要素の並びは `xbrl_presentation_roles`（書類×区分で 1 行）に入れ、`xbrl_filings.presentation_status` に済みの印を付ける。過去分は `jobs/xbrl/xbrl_presentation.py` を 1 回流して埋める（「表示リンクの区分」および「過去の書類の表示リンクを埋める」を参照）。
- 短信 PDF しか存在しない期の行と XBRL 要素の対応は `xbrl_pdf_matches`（PDF の fact ごとに 1 行、付け直しは `tools/tdnet_pdf/matches.py`）に保存し、有報・短信 XBRL・短信 PDF を同じ要素名で横断検索する SQL ビューとして `xbrl_statement_values` を持つ（「出どころをまたいで要素名で引く」を参照）。テーブル・索引（`xbrl_elements` の `local_name` と `'ext:' || local_name`）およびビューは `init_db` と `jobs/migrate.py` が管理し、定義文字列が変わればビューを作り直す。リポジトリ内の SQL ビューはこれ 1 つだけである。

### 企業の識別

`stocks` テーブルに法人番号は無く、KAB-72 で追加された `stocks.edinet_code` も `NULL` 許容であるため、XBRL 層の企業キーには 5 桁の証券コード（`stocks.code`）だけを使う。EDINET 書類は `edinet_documents.code` を経由して `stocks.code` に紐付く。

```
TDnet company_code または保存ディレクトリ {code4}
        │  to_code4 + 末尾 0（app/xbrl/codes.py の jq_lookup_code と同じ）
        ▼
stocks.code   例: 7203 → 72030、262A → 262A0
```

`stocks` に存在しないコードの ZIP とインフラファンドの ZIP は `_unmatched/` ディレクトリに保存されており、パース対象から除外する。

### コード規則

証券コードの形式に関する規則は以下のとおりである。

- DB の保存・結合キーは常に 5 桁コード（正規表現 `^[0-9][0-9A-Z]{3}[0-9]$`）とする。
- ファイル保存ディレクトリと `manifest.jsonl` の `code4` フィールドは 4 桁のままとし、画面表示用の 4 桁コードと混同しない。

### 禁止事項

設計・実装上の禁止事項は以下のとおりである。

- `companies` テーブルを新設して `stocks` と二重マスタにしてはならない。
- `stocks.edinet_code` を必須列にしてはならない。
- 勘定科目を横持ちの列にしたテーブルを新設してはならない。
- `xbrl_financials` に対して手作業で `INSERT` する処理を書いてはならない。
- マッピング規則を Python コード内にハードコードしてはならない。
- `namespace_uri` の完全一致でマッピングを解決してはならない。
- パース時にマッピング外の要素を黙って捨ててはならない（長文テキストの値だけを除き、`xbrl_elements` と `xbrl_facts` にはすべて残す）。
- 「初期定義（最小セット）」に無い科目のマッピングを確認なしに追加してはならない。
- `app/xbrl/statements.py`（`jobs/xbrl/xbrl_statements.py`）以外の処理から XBRL の数値で `statements` を上書きしてはならない。
- vendoring している `app/xbrlp/` 本体を改変してコンテキスト解釈を混ぜ込んではならない（必ず `app/xbrl/` のラッパ側で実装する）。
- `Arelle` を依存パッケージに追加してはならない。

## 取得と解析

### 短信 ZIP の取得（fetch）

担当ジョブは [`xbrl/tdnet_xbrl_r2`](gen/xbrl.md#xbrltdnet_xbrl_r2)（`jobs/xbrl/tdnet_xbrl_r2.py`）である。TDnet の適時開示一覧を走査して XBRL ZIP を取得し、`{dest}/{code4}/YYYYMMDD_..._{xbrl_id}.zip` と日次カタログ `manifest/{YYYY-MM-DD}.jsonl` を出力する。

- 引数は遡及日数 `N` であり、定常 cron は `2`（前日と当日）を渡して前日 23 時以降の開示を翌日に拾う。
- 重複取得スキップの正本は遡及期間内 `manifest/*.jsonl` の `xbrl_id` である。`manifest` の 1 行は `xbrl_id`・`code`・`code4`・`pubdate`・`time`・`title`・`path`・`bytes`・`sha256`・`fetched_at` の 10 項目を持つ。
- `stocks` に無い銘柄とインフラファンド（商品区分 `013`・`022` で名称に「インフラ」を含むもの、商品区分が空の場合は社名に「インフラ投資法人」を含むもの）は `{dest}/_unmatched/` に保存し、削除はしない。
- 後続の `xbrl_parse.py` は `manifest` を入力カタログとして読み、取得ジョブ側には `parse_status` を持たせない。

### パーサ（`xbrlp`）

#### 採用理由と配置

`Arelle` は 1 ファイルあたり数秒を要するが、`xbrlp`（取得元は `https://gist.github.com/kawasin73/346ed6a24382a2b238e1ff0d10f3f40c` の `__init__.py`・`file_loader.py`・`parser.py`）は Python 標準ライブラリだけで iXBRL の fact・ラベル・表示リンク・計算リンクを約 20 倍高速に読み取れる。依存を増やさない方針にも合致するため、外部 git submodule は使わず `app/xbrlp/` にコピーしてリビジョンを固定する。KAB-60 でネットワークダウンロードを廃止し同梱ディレクトリだけを読むようにした `app/xbrlp/file_loader.py` 以外は本体を改変しない。

主な配置は次のとおりである。`app/xbrl/` にはこの木に無い `codes.py`・`gross_profit.py`・`jobs.py`・`store.py`・`shareholders.py`・`pdf_matches.py`・`revisions.py` と、パッケージ `pdf/`（`__init__.py`・`_layout.py`・`_tanshin.py`）もある。

```
app/xbrlp/                    # vendored パーサ本体
app/xbrl/
  __init__.py
  parser.py                   # ZIP 展開・iXBRL 特定・xbrlp 呼び出し・context 解釈
  context.py                  # 期末日・相対期間・タイトル分類・会計基準推定
  presentation.py             # *pre.xml の財務 3 表区分と深さ・見出し表示名の抽出
  presentation_backfill.py    # 過去書類の表示リンク埋め直し
  documents.py                # 短信原文 HTML の抽出と表示時サニタイズ
  mappings.py                 # YAML マッピングの DB 同期
  normalize.py                # xbrl_financials / 大株主 / セグメント正規化の統括
  segments.py                 # 報告セグメント注記の正規化
  statements.py               # statements テーブルへの行生成・マージ
  three_statements.py         # 有報・短信 XBRL・短信 PDF 横断の財務 3 表組み立て
  statement_values.py         # xbrl_statement_values ビュー定義
config/xbrl/
  std_items.yaml
  mappings/
    pl_jpgaap.yaml
    bs_jpgaap.yaml
    cf_jpgaap.yaml
    ifrs.yaml
    annual_report.yaml
    segments.yaml
    tdnet_pdf.yaml
```

#### `xbrlp` が提供するものとラッパの責務

`xbrlp` が提供するインターフェースは以下のとおりである。

```python
from app.xbrlp import Parser, FileLoader

parser = Parser(file_loader=FileLoader(cache_dir=cache_dir), shared_xsd_cache=shared)
parser.prepare_ixbrl(manifest_xml_path)
for fact in parser.load_facts():
    fact.qname.prefix          # namespace_key 相当
    fact.qname.local_name
    fact.qname.namespace_uri
    fact.context_ref
    fact.unit_ref
    fact.is_numeric
    fact.is_nil
    fact.scale, fact.sign, fact.format
    fact.value                 # scale・sign 適用済みの Decimal または str
```

一方、以下の要素は `xbrlp` が提供しないため、`app/xbrl/` 側のラッパが担う。

- `xbrli:context` の期間（`period`）・主体（`entity`）・次元（`scenario`・`segment`）は、同一 iXBRL を `xml.etree.ElementTree` で走査して抽出する（`iterparse` 後に空になる `Fact.raw_element` には依存しない）。
- `xbrli:unit`（`unitRef` から `JPY`・`JPYPerShares` 等への解決）もラッパ側で抽出する。
- `ix:nonFraction` を持たない従来の `.xbrl` インスタンス文書と `AuditDoc` は対象外とする。

### TDnet ZIP 内の対象ファイル

ZIP を一時ディレクトリへ展開したあと、以下の順序で解析対象の iXBRL ファイルを特定する。

1. `Summary/` 配下を先頭に置き、ZIP 内の `*.htm`・`*.html`・`*.ixbrl` のうち `ix:nonFraction` または `ix:nonNumeric` を含むファイルを列挙する（`find_ixbrl_files`）。
2. `*manifest*.xml` が存在し `<ixbrl>` 要素を列挙していれば追加する（`Attachment` の manifest しか無い場合でも `Summary` 側のファイルを捨てない、KAB-48）。
3. 1 の走査では `AuditDoc/` など監査関連のパスを除外する。2 の manifest は `find_manifest` が ZIP 内で最初に見つけた 1 つだけを読むので、それが `AuditDoc/` の manifest なら監査報告書の iXBRL が和に入り、`PublicDoc/` の manifest は読まれない（`PublicDoc/` の iXBRL は 1 の走査で拾う）。
   - ZIP に別の書類の iXBRL・xsd・リンクベースが一緒に入っている書類は `config/xbrl/zip_manifest_only.json`（パスは `app/config.py` の `XBRL_ZIP_MANIFEST_ONLY_PATH`）に書類の番号で載せ、規則で自動に見分けない。載せた書類だけは、manifest のあるフォルダで manifest の `instance` の `preferredFilename` の名前を含まない iXBRL・xsd・xml・xbrl を読まない（`app/xbrl/zip_scope.py`。展開した直後に `extract_zip` が消すので `parse_package`・タクソノミの走査・表示リンクも同じものを読む。メモリで ZIP を読む表示リンクの埋め直し `read_zip`・原文 `read_documents`・EDINET のテキストブロックも同じ一覧に従う）。一覧に無い書類は今までどおり和を読む（`5233` の訂正有報 `S100W2KO`［2020-03 期］の ZIP に同じ日に出した 2024-03 期の訂正有報の一式が入り、2024-03 の値が 2020-03 の文脈に束ねられた。KAB-592）。
4. 対象 iXBRL が 0 件だった場合（従来の `.xbrl` インスタンスのみの ZIP を含む）は、`parse_status='skipped'`・`parse_error='no-ixbrl'` として記録する。

タクソノミと並列実行・コンテキスト抽出の扱いは以下のとおりである。

- タクソノミはリポジトリ同梱の `XBRL_CACHE_DIR`（既定 `data/xbrl_taxonomy`）だけを読み、`urlopen` による外部取得は一切行わない。参照先の XSD やラベル linkbase が欠けている filing は `parse_status='failed'` にして欠落パスをログに出し、`tools/xbrl/taxonomy_bundle.py`（`check`・`sync` で `schemaRef` → `xsd:import` と `load_labels` の参照先を走査し、汎用ラベル・定義リンク・コメント内 URL は数えない）で過去年版を含め同梱を補充したうえで `xbrl_parse.py --retry-failed` で復旧する。
- プロセス内ではリモート XSD とラベル linkbase を共有キャッシュに載せる（`parse_package`）。ラベル linkbase は href ごとに `(prefix, local)` → 最初の日本語・英語ラベルへ縮約し、filing ごとに fact に出現する要素と次元メンバーだけを引く（KAB-63）。DB 書き込み時は要素を filing 単位で一括 `SELECT`・`INSERT`・差分 `UPDATE` し、コンテキストと fact は bulk insert する。
- `xbrl_parse.py` は DB に触らない ZIP 展開・解析（`parse_zip`）を `XBRL_PARSE_WORKERS` 個（既定 `2`、`1` で直列、DB 書き込みが 1 件約 `30ms` で律速するため `3` 以上にしてもほぼ速くならない）のプロセスで先読みし、DB 書き込みは親プロセス 1 本が `manifest` 順に直列で行う（KAB-63）。順序が保たれるため直近 filing からの会計基準引き継ぎは直列と一致し、書き込み順が来た時点で skip 判定を再確認し、ワーカークラッシュ時は親で再パースする。
- コンテキスト抽出は iXBRL から `xbrli:context` ブロックだけを切り出してパースし、コメント・CDATA・ENTITY がある場合や開始タグ数とブロック数が合わない場合、パース失敗時は全体の `iterparse` にフォールバックする（`contextRef` だけを持つ添付本表はコンテキスト 0 件になる）。

### コンテキスト解釈

#### 期間と次元

コンテキストの期間と次元は以下の規則で抽出する。

- `xbrli:context` の期間が `instant` なら `period_type='instant'`・`end_date=instant`・`start_date=NULL` とし、`startDate` と `endDate` の組なら `period_type='duration'` とする。
- `scenario` および `segment` 内の `explicitMember` を `{"prefix:axisLocalName": "prefix:memberLocalName"}` の辞書にして JSON 文字列（`dimensions_json`）で保存し、空でなければ `has_dimensions=1` とする。

#### 連結・単体の判定（`is_consolidated`）

連結・単体の別は以下の優先順で判定する。

1. 次元メンバーに `ConsolidatedMember` を含む場合は `1`（連結）とする。
2. 次元メンバーに `NonConsolidatedMember` を含む場合は `0`（単体）とする。
3. どちらも含まない場合は短信の既定に合わせて `1`（連結）とし、単体と断定できるときだけ `0` にする。正規化では `0` と `1` の両方を対象とし、`NULL` は残さない。

#### 当期・前期の判定（`relative_period`）

filing の当期期末日は、次の優先順で最初に取れた日付とする。本表または DEI がある書類ではサマリー実績を期末判定に使わない（KAB-59）。

1. DEI の当期期末・対象期間期末（`CurrentPeriodEndDateDEI` など）を採用する。
2. 年次タイトルに含まれる `YYYY年M月期` から期末日を算出する。
3. 本表 PL（連結・単体次元のみ）の主要金額要素の `duration` 期末日を採用する。
4. 本表も DEI も無い書類に限り、短信サマリー（`ResultMember`）の `duration` 期末日を採用する。
5. 全 `duration` コンテキストの最新 `end_date` を採用する。

コンテキストの `end_date` がこの当期期末日と一致すれば `current`、1 年前と `±30` 日以内なら `prior1`、2 年前と `±30` 日以内なら `prior2`、それ以外は `NULL` とする。

#### タイトルからの書類種別・会計基準判定

`app/xbrl/context.py` の `classify_title` は、NFKC 正規化したタイトル文字列から `is_amendment`（`訂正` を含むか）と `doc_type`・`period_type` を以下の順で判定する。

| タイトルの条件 | `doc_type` | `period_type` |
|---|---|---|
| `業績予想` かつ `修正`、または `EarnForecastRevision` を含む | `forecast_revision` | タイトル内の期間語（無ければ `FY`） |
| `配当予想` かつ `修正`、または `DividendForecastRevision` を含む | `dividend_revision` | タイトル内の期間語（無ければ `FY`） |
| `四半期` を含む | `quarterly` | `第1` なら `Q1`、`第2` なら `Q2`、`第3` なら `Q3`、無ければ `NULL` |
| `半期` または `中間` を含む | `semiannual` | `Q2` |
| `決算短信` または `有価証券報告書` を含む | `annual` | `FY` |
| 上記以外 | `other` | `NULL` |

会計基準 `accounting_standard` は、タイトルに `IFRS`・`SFRS`（指定国際会計基準）・`国際会計` があれば `'IFRS'`、`米国` があれば `'USGAAP'`、`日本基準` があれば `'JPGAAP'` とする。タイトルから決まらない場合は DEI の会計基準要素テキストを参照し、それも無ければ名前空間（`jpigp`・`ifrs` または非 nil の数値 `*IFRS` fact があれば `'IFRS'`、`us-gaap`・`usgaap` があれば `'USGAAP'`、両方が混在すれば `None`）から推定し、なお不明であれば同一銘柄の直近 `parsed` filing から引き継ぐ（根拠が無いまま `'JPGAAP'` とは決め打ちしない）。

### parse の手順

担当ジョブは [`xbrl/xbrl_parse`](gen/xbrl.md#xbrlxbrl_parse)（`jobs/xbrl/xbrl_parse.py`）である。サブコマンドは `parse`（既定）・`load-mappings`・`report-unmapped`（`--limit` 既定 `50`）であり、`parse` 時は `--from YYYY-MM-DD`・`--to YYYY-MM-DD`・`--doc-id <xbrl_id>`・`--retry-failed`・`--reparse`・`--dest` を受け取る。TDnet へは通信しないためレート制限は不要であり、`errors=` と非ゼロ終了が既存の Slack バッチアラートに連携する。1 件あたりの処理手順は以下のとおりである。

1. `manifest` 行を読み、`path` の ZIP が存在しなければエラー件数を加算して次へ進む。
2. `jq_lookup_code(code or code4)` が `stocks` に存在しなければスキップする（`_unmatched` は対象外）。
3. `xbrl_filings` に同一 `(source='tdnet', doc_id)` で `parse_status='parsed'` かつ `raw_sha256` が一致する行があり、`--reparse` でなければスキップする。
4. ZIP を一時ディレクトリへ展開し（`data/` 上には展開しない）、対象 iXBRL から `xbrlp` で fact・label を、XML 走査で context・unit を抽出する。
5. `xbrl_elements` を `namespace_uri + local_name` で UPSERT する（`namespace_key` は prefix）。名前空間 URI が `disclosure.edinet-fsa.go.jp/taxonomy/` または `www.xbrl.tdnet.info/taxonomy/` 配下のものを標準要素（`is_custom=0`）とし、URI が取れない fact に限り `namespace_key` の完全一致（`STANDARD_NS_KEYS`: `jppfs_cor`・`jpcrp_cor`・`jpdei_cor`・`jpigp_cor`・`jpctl_cor`・`jplvh_cor`・`jpsps_cor`・`tse-ed-t`・`tse-t-ed` の 9 種、勝手に追加しない）にフォールバックする。EDINET の会社独自名前空間（`jpcrp030000-asr_E...`）が誤って標準扱いにならないよう部分一致は行わず、既存行の補正は `jobs/migrate.py` が行う（KAB-81）。
6. `xbrl_facts` を一括 `INSERT` する。数値は `scale`・`sign` 適用済みの `Decimal` を `value_num` に入れ、非数値は `XBRL_TEXT_MAX_CHARS`（既定 `1000` 文字）以内だけを `value_text` に入れる（上限を超える長文は fact 行を作らない）。`xsi:nil` は `is_nil=1`・値 `NULL` とする。
7. 表示リンク（`AuditDoc/` 以外の `*pre.xml`）から財務 3 表の区分を抽出して `xbrl_presentation_roles` に保存し、`presentation_status` を設定する（読めない・区分が無い場合も 0 行になるだけで取り込みは止めない）。
8. 成功したら `parse_status='parsed'` でコミットし、続けて原文 HTML の保存と、同一開示番号（`doc_id` 末尾 14 桁）の `tdnet_pdf` filing の同期削除（`sync_pdf_filing`、例外が起きても ZIP 側を `failed` にはしない）を行う。パース中の例外は filing 単位でロールバックして `parse_status='failed'` と要約トレースを記録し、ジョブ全体は止めない。再パース時は SQLite で FK カスケードに頼らず、`delete_filing_payload` で既存の PDF 対応・表示リンク・fact・context を明示的に `DELETE` してから入れ直す。

### 表示リンクの区分

書類 ZIP 内の表示リンク（`link:presentationLink`）は、どの要素がどの財務表に属し、どの順序・階層で印字されるかを提出会社が宣言した定義である。`app/xbrl/presentation.py` の `read_presentation` が展開済みフォルダの全区分を読み、`statement_roles` が財務 3 表に該当する区分だけを残して `xbrl_presentation_roles`（`edinet` と `tdnet` の書類のみ、表示リンクの無い `tdnet_pdf` は行を持たない）へ取り込みごとに入れ替える。

#### 区分名から表への対応と要素の並び

区分名から財務 3 表への振り分けと要素並びの構築規則は以下のとおりである。

- 対象とする区分は、role URI 末尾の区分名が `rol_` で始まり、注記（`rol_Notes...`）および株主資本等変動計算書（`...ChangesInEquity...`・`...ChangesInNetAssets...`・`...UnitholdersEquity...`）でないもののうち、CF が `StatementOfCashFlows`、BS が `BalanceSheet` または `StatementOfFinancialPosition`、PL が `StatementOfIncome`・`StatementOfProfitOrLoss`・`StatementOfComprehensiveIncome`（包括利益計算書も PL）を含むものである。
- 区分名に `Consolidated` があれば連結（`is_consolidated=1`）、`IFRS` があれば IFRS（`is_ifrs=1`）とし、前後の `Quarterly`・`SemiAnnual`・`Type1SemiAnnual`・`YearToQuarterEnd`・`Condensed`・`TDnet`・`-indirect` は見ない。1 つの書類に日本基準と IFRS の 2 組の個別表がある会社も `is_ifrs` で分かれ、後から分け方を直せるよう生の区分名も保存する。複数の `*pre.xml` に同一区分があれば最初の区分の後ろに連結し、書類内での保存順を `seq` に持つ。
- 要素の並びは `loc` の出現順ではなく、`presentationArc` の親子を `order` 順に深さ優先で辿った順とする。同じ要素に子用と親用で別ラベルの `loc` を持つ書類があるため、親子は `loc` ラベルではなく要素で結ぶ。根が複数あれば `loc` 順とし、`use="prohibited"` の arc は同じ arc を打ち消す。CF の期首・期末現金のように同一要素が 2 回出る場合は両方残し、`preferredLabel` の種類（`periodStartLabel`・`periodEndLabel`・`totalLabel` など）で見分ける。末尾が `Heading`・`Table`・`Axis`・`Member`・`LineItems` の構造要素は落とし、`...Abstract` の見出し要素は残す。

#### 深さ・見出し表示名・名前空間と `presentation_status`

`items_json` の各要素タプルは `[namespace_key, local_name, is_custom, preferred_label, depth, label_ja]` の最大 6 要素を持つ。

- 5 つ目の `depth` は区分の根からの段数（`0` 始まり）である。残した祖先の数だけを数え、落とした `Heading`・`Table`・`LineItems` などは段数に含めない（含めると `...Table` の子が兄弟より深くなり並びだけからは親を取り違える）。複数の `*pre.xml` を連結するときもそれぞれの根から数える。
- 6 つ目の `label_ja` は `Abstract` を含む見出し要素の表示名である（通常要素は `xbrl_elements.label_ja` で足りるため `null`）。arc の `preferredLabel` のフル URI（`.../jppfs/Consolidated/role/label` の「その他の包括利益累計額」のように末尾だけでは区別できない役割があるためフル URI のまま使う）、無ければ標準ロール（`http://www.xbrl.org/2003/role/label`）の日本語ラベルを、提出会社の `*lab.xml` → `XBRL_CACHE_DIR` 内の標準タクソノミラベルリンクの順で引く（英語の `-en.xml` は読まず、標準ラベルリンクはプロセス内で 1 回だけ読む）。書類・連結区分ごとに名前が変わるため `xbrl_elements` には入れない。深さ・表示名追加前の 4 要素行では「財務3表」が 2 段の既定見出しを出し、5 要素行では要素名を出す（`--redo-flat` で読み直せる）。
- 要素の名前空間は `loc` の `xlink:href` の `#` 後ろ（xsd の要素 `id`）から引く。提出会社の xsd を指すときは `name` 属性を `local_name`・残りを `namespace_key`（`jpcrp030000-asr_E...` や `tse-...-証券コード`、`is_custom=1`）とし、`STANDARD_NS_URI_ROOTS` 配下の標準タクソノミはファイル名から日付を落とした名前（`jppfs_cor` など、`is_custom=0`）にする。

書類ごとの処理状態 `xbrl_filings.presentation_status`（`jobs/migrate.py` が `ALTER TABLE ... ADD COLUMN` で追加）の意味を下表に示す。

| `presentation_status` | 意味 |
|---|---|
| `NULL` | まだ表示リンクを読んでいない（`delete_filing_payload` 実行時も `NULL` に戻る） |
| `'ok'` | 財務 3 表の区分を 1 つ以上保存した |
| `'none'` | 読んだが財務 3 表の区分が無かった（予想修正、CF 区分の無い有報・Q1 や Q3 の報告書・短信、`*pre.xml` 無しの ZIP、`tdnet_pdf` の filing） |
| `'gone'` | バックフィル時に原本 ZIP が入手できなかった |
| `'failed'` | ZIP または表示リンクの読み取りに失敗した |

## 正規化

### 標準勘定科目とマッピング

標準勘定科目は `config/xbrl/std_items.yaml`、要素から標準科目への対応は `config/xbrl/mappings/*.yaml` を正本とし、コード内には科目名をハードコードしない。マッピングの `ns` には URI ではなく `namespace_key` を書き、`priority` の小さい順に優先する。未マッピングの標準タクソノミ要素は `xbrl_parse.py report-unmapped`（`fact_count DESC`）で確認し、エージェントが独断でマッピングを足してはならない。

```yaml
- std_item: net_sales
  elements:
    - { ns: jppfs_cor, name: NetSales,          priority: 10 }
    - { ns: jppfs_cor, name: OperatingRevenue1, priority: 20 }
```

### 初期定義（最小セット）

第 1 弾の BS・PL・CF 23 項目に加え、KAB-47 で EPS・配当・業績予想、KAB-77 でセグメント 4 項目（`SEG`）、KAB-78 で有報の項目を追加した全 41 項目を `config/xbrl/std_items.yaml` に定義している。これ以外の科目は確認なしに追加しない（合成前提の `interest_bearing_debt` も初期 YAML には含めない）。TDnet 短信 ZIP は BS・CF が薄いサマリ中心の書類も多いため、`is_required` は `total_assets`・`net_sales`・`operating_income`・`net_income` の 4 項目にだけ付ける。

#### BS（`statement: BS`、時点 `instant`、9 項目）

貸借対照表・財政状態計算書の標準科目を下表に示す。

| `code` | `label_ja` | 代表的な `local_name` |
|---|---|---|
| `cash_and_deposits` | 現金及び預金 | `CashAndDeposits` |
| `current_assets` | 流動資産合計 | `CurrentAssets`, `CurrentAssetsIFRS` |
| `noncurrent_assets` | 固定資産合計 | `NoncurrentAssets`, `NonCurrentAssetsIFRS` |
| `total_assets` | 資産合計 | `Assets`, `AssetsIFRS`（`is_required: true`） |
| `current_liabilities` | 流動負債合計 | `CurrentLiabilities`, `TotalCurrentLiabilitiesIFRS` |
| `noncurrent_liabilities` | 固定負債合計 | `NoncurrentLiabilities`, `NonCurrentLabilitiesIFRS` |
| `total_liabilities` | 負債合計 | `Liabilities`, `LiabilitiesIFRS` |
| `shareholders_equity` | 株主資本合計 | `ShareholdersEquity`, `EquityAttributableToOwnersOfParentIFRS` |
| `net_assets` | 純資産合計 | `NetAssets`, `EquityIFRS` |

#### PL（`statement: PL`、期間 `duration`、10 項目）

損益計算書の標準科目を下表に示す。日本基準の有報は PL 本表に EPS の数値 fact が無いため、KAB-78 で「主要な経営指標等」の `jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults`・`DilutedEarningsPerShareSummaryOfBusinessResults`（`priority: 50`）および IFRS・米国基準版（`...IFRSSummaryOfBusinessResults`・`...USGAAPSummaryOfBusinessResults`、`priority: 40`）を追加しており、連結・単体の両方が入る。

| `code` | `label_ja` | 代表的な `local_name` |
|---|---|---|
| `net_sales` | 売上高 | `NetSales`, `OperatingRevenue1`, `GrossOperatingRevenue`（内訳の売上高より優先）, `OrdinaryIncomeBNK`, `OperatingRevenue2`, `Revenue`, `OperatingRevenueRWY`, `OperatingRevenueELE`, `OperatingRevenueSEC`, `NetSalesOfCompletedConstructionContractsCNS`, `RevenueIFRS`, `NetSalesIFRS`, `Revenue2IFRS`、およびサマリーの `tse-ed-t:NetSales`・`OperatingRevenues`・`GrossOperatingRevenues`・`Revenue`・`SalesIFRS`（`is_required: true`） |
| `cost_of_sales` | 売上原価 | `CostOfSales`, `CostOfSalesIFRS` |
| `gross_profit` | 売上総利益 | `GrossProfit`, `GrossProfitIFRS` |
| `sga` | 販売費及び一般管理費 | `SellingGeneralAndAdministrativeExpenses`, `SellingGeneralAndAdministrativeExpensesIFRS` |
| `operating_income` | 営業利益 | `OperatingIncome`, `OperatingProfitLossIFRS`（`is_required: true`） |
| `ordinary_income` | 経常利益 | `OrdinaryIncome` |
| `profit_before_tax` | 税引前当期純利益 | `IncomeBeforeIncomeTaxes`, `ProfitLossBeforeTaxIFRS` |
| `net_income` | 親会社株主に帰属する当期純利益 | `ProfitLossAttributableToOwnersOfParent`, `ProfitLossAttributableToOwnersOfParentIFRS`, `ProfitLossIFRS`（単体 IFRS、`is_required: true`） |
| `eps_basic` | 1株当たり当期純利益 | `tse-ed-t:NetIncomePerShare`, `BasicEarningsLossPerShare`, `BasicEarningsLossPerShareIFRS` |
| `eps_diluted` | 潜在株式調整後1株当たり当期純利益 | `DilutedNetIncomePerShare`, `DilutedEarningsLossPerShareIFRS` |

#### CF（`statement: CF`、期間 `duration`［期末現金のみ時点］、6 項目）

キャッシュ・フロー計算書の標準科目を下表に示す。

| `code` | `label_ja` | 代表的な `local_name` と備考 |
|---|---|---|
| `operating_cf` | 営業活動によるCF | `NetCashProvidedByUsedInOperatingActivities`, `CashFlowsFromUsedInOperatingActivitiesIFRS` |
| `investing_cf` | 投資活動によるCF | `NetCashProvidedByUsedInInvestmentActivities`, `CashFlowsFromUsedInInvestingActivitiesIFRS` |
| `financing_cf` | 財務活動によるCF | `NetCashProvidedByUsedInFinancingActivities`, `CashFlowsFromUsedInFinancingActivitiesIFRS` |
| `capex` | 設備投資額 | `PurchaseOfPropertyPlantAndEquipment`, `PurchaseOfPropertyPlantAndEquipmentInvCFIFRS`（`sign: invert`） |
| `depreciation` | 減価償却費 | `DepreciationAndAmortizationOpeCF`, `DepreciationAndAmortizationOpeCFIFRS` |
| `cash_end` | 現金及び現金同等物期末残高 | `CashAndCashEquivalents`, `CashAndCashEquivalentsIFRS` |

#### 配当・比率・有報の科目（`statement: OTHER`、12 項目）

1 株当たり配当金、短信サマリ・有報の経営指標、および有報固有の科目（KAB-78、`config/xbrl/mappings/annual_report.yaml`）を下表に示す。会社予想は新規の `std_item` を増やさず、`net_sales`・`operating_income`・`ordinary_income`・`net_income`・`eps_basic`・`dps` を `value_kind='forecast'`（修正前は `'forecast_previous'`、通期は `target_period='FY'`、中間は `'H1'`、レンジは `range_side='lower'`・`'upper'`）として保持する。単位は原則 fact の単位（USD 開示は `USD`・`USDPerShares`）を使い、fact が `pure` または空のときだけ `std_items.yaml` の `unit`（`persons`・`years`・`ratio`）を使う。

| `code` | `label_ja` | 既定 `unit` | 主な要素（`priority` の小さい順） | 連結・単体 |
|---|---|---|---|---|
| `dps` | 1株当たり配当金 | `JPYPerShares` | `tse-ed-t:DividendPerShare`（実績・予想は `value_kind`、期は `target_period`） | 単体 |
| `bps` | 1株当たり純資産 | `JPYPerShares` | `tse-ed-t:NetAssetsPerShare`、有報は `jpcrp_cor:EquityToAssetRatioIFRSSummaryOfBusinessResults`（要素名に反しラベルは「１株当たり親会社所有者帰属持分」）・`EquityAttributableToOwnersOfParentPerShareUSGAAPSummaryOfBusinessResults` → `NetAssetsPerShareSummaryOfBusinessResults` | 両方 |
| `equity_ratio` | 自己資本比率 | `ratio`（`0.471` ＝ 47.1%） | `tse-ed-t:EquityToAssetRatio`、有報は `jpcrp_cor:RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults`・`EquityToAssetRatioUSGAAPSummaryOfBusinessResults` → `EquityToAssetRatioSummaryOfBusinessResults` | 両方 |
| `roe` | 自己資本利益率 | `ratio` | `tse-ed-t:RateOfReturnOnEquity`、有報は `jpcrp_cor:RateOfReturnOnEquityIFRSSummaryOfBusinessResults`・`...USGAAPSummaryOfBusinessResults` → `RateOfReturnOnEquitySummaryOfBusinessResults` | 両方 |
| `shares_issued` | 発行済株式総数 | `shares` | `jpcrp_cor:TotalNumberOfIssuedSharesSummaryOfBusinessResults`（提出会社の期末時点） | 単体 |
| `treasury_shares` | 自己株式数 | `shares` | `jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc`（自己株式等の表の計、所有者別の `Row*Member` は除外） | 連結（軸なし context） |
| `employees` | 従業員数 | `persons` | `jpcrp_cor:NumberOfEmployees`（セグメント別は除外） | 両方 |
| `employee_avg_age` | 平均年齢 | `years` | `jpcrp_cor:AverageAgeYears...` ＋ `AverageAgeMonths...`（`part: months` で「43 歳 10 ヶ月」を `43.83` に合成） | 単体 |
| `employee_avg_tenure` | 平均勤続年数 | `years` | `jpcrp_cor:AverageLengthOfServiceYears...` ＋ `...Months...`（同上） | 単体 |
| `employee_avg_salary` | 平均年間給与 | `JPY` | `jpcrp_cor:AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees` | 単体 |
| `rd_expenses` | 研究開発費 | `JPY` | `jpcrp_cor:ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities`（研究開発活動の節）→ `jpigp_cor:ResearchAndDevelopmentExpenditureRecognizedAsExpenseDuringPeriodIFRS` → `jpcrp_cor:ResearchAndDevelopmentExpensesIncludedInGeneralAndAdministrativeExpensesAndManufacturingCostForCurrentPeriod`（注記）→ `jppfs_cor:ResearchAndDevelopmentExpensesSGA` | 両方 |
| `capex_reported` | 設備投資額（設備の状況） | `JPY` | `jpcrp_cor:CapitalExpendituresOverviewOfCapitalExpendituresEtc`（設備投資等の概要の合計、セグメント別しか無い会社は空欄） | 連結 |

有報科目の設計上の注意点と実データ検証結果（2026 年 10 月 23 日時点の本番 EDINET 有報：日本基準 `7,838` 件・IFRS `667` 件・米国基準 `12` 件、研究開発活動の節は日本基準 `4,187` 件、設備投資等の概要の合計は `7,150` 件、平均年齢の月要素は `544` 件［6%］）は以下のとおりである。

- `capex_reported` は無形資産を含む発生ベースの額であり、CF の有形固定資産取得支出を符号反転した `capex` とは一致しない。研究開発活動の節を「221 億円」のように丸めて書く会社（注記の `22,134` 百万円と 1 億未満ずれる）も有報の記載どおりに入れる。主要な経営指標等の前期以前（`Prior1`〜`Prior4`）、提出日現在の発行済株式数（`FilingDateInstant`）、セグメント別従業員数、PER・配当性向・株主総利回りは取らない。
- 第 1 回検証（2025 年 6 月提出の有報 155 社：日本基準 118・IFRS 33・米国基準 4）では、有報 HTML の表示値と上記科目＋EPS の計 `2,701` 行がすべて一致した。IR Bank `get_financials`（百万円）とは EPS `155` 社中 `155` 社一致、自己資本比率 `153` 社一致（2 社は IR Bank の独自計算値）、研究開発費 `84` 社一致・`29` 社ずれ（丸め記載や販管費限定）、設備投資額 `129` 社一致・`12` 社ずれ（IFRS で CF 値を採る会社等）・`4` 社空欄（セグメント別のみ）であり、BPS×（発行済株式数−自己株式数）と自己資本比率×総資産の整合は `142` 社中 `136` 社で 3% 以内（残り 6 社は期末後株式分割や優先株）であった。
- 第 2 回検証（2025 年提出の別抽出 100 社：日本基準 95・IFRS 5、3 月決算 55・12 月決算 12・9 月決算 7 ほか）でも、有報 HTML 表示と全 `1,595` 行が一致し、IR Bank（期が取れた 98 社）とは EPS `97` 社・自己資本比率 `95` 社・研究開発費 `49` 社一致（`3` 社ずれ）・設備投資額 `83` 社中 `83` 社一致、BPS 整合は `100` 社中 `95` 社で 3% 以内であった。

### IFRS・業種

IFRS および業種別科目の扱いは以下のとおりである。

- IFRS は `jpigp_cor` 等の名前空間を使う。`accounting_standard='IFRS'` 指定のマッピングは原則として filing の基準一致時のみ使うが、要素名が `*IFRS` で終わる場合は filing の基準が `'JPGAAP'` や `NULL` でも使う（KAB-55、`40530` の予想）。
- 日本の制度上、IFRS 提出会社の単体決算書は日本基準のため 1 つの短信に両基準の本表が同居することがあるが（`436A0`）、IFRS の書類では `jppfs_*` 要素を正規化に使わず、経常利益など IFRS 側に対応が無い科目は空欄にする（KAB-52）。KAB-73 で追加した IFRS 本表要素（`CurrentAssetsIFRS`・`NonCurrentAssetsIFRS`・`TotalCurrentLiabilitiesIFRS`・タクソノミ綴りどおりの `NonCurrentLabilitiesIFRS`・`LiabilitiesIFRS`・`shareholders_equity` に対応する `EquityAttributableToOwnersOfParentIFRS`・売上原価・売上総利益・販管費・投資および財務 CF・`PurchaseOfPropertyPlantAndEquipmentInvCFIFRS`・`DepreciationAndAmortizationOpeCFIFRS`・`CashAndCashEquivalentsIFRS`）は TDnet の IFRS 添付本表にも効く。
- 銀行等の営業収益・建設業の完成工事高・鉄道や電力や証券の営業収益・小売の営業総収入はマッピングの `priority` フォールバックで吸収し、業種分岐コードは書かない。

### マッピングの投入（`load-mappings`）

`config/xbrl/` の YAML を編集したときは以下のコマンドで `xbrl_std_items` と `xbrl_element_mappings` を差分 UPSERT する（YAML から消えた行は削除される）。

```bash
python jobs/xbrl/xbrl_parse.py load-mappings
```

### 正規化の手順（`normalize`）

担当ジョブは [`xbrl/xbrl_normalize`](gen/xbrl.md#xbrlxbrl_normalize)（`jobs/xbrl/xbrl_normalize.py`）である。filing 単位で既存の `xbrl_financials` を削除してから入れ直す。選定規則は以下のとおりである。

1. 連結・実績予想・レンジ・配当以外の次元が付いた fact は捨てる（セグメントは `xbrl_segments` 専用とし、`statement='SEG'` の科目は `xbrl_financials` に使わない）。DEI `WhetherConsolidatedFinancialStatementsArePreparedDEI` が `false` の有報では、連結軸が無いコンテキスト（設備の状況・研究開発活動・自己株式等）も単体（`is_consolidated=0`）にする。
2. レガシー 23 項目（BS・PL・CF）の実績は、原則として `has_dimensions` が空または連結・単体のみで `relative_period='current'` の本表コンテキストから `std_item` ごとに `priority` 昇順で 1 値を選ぶ（`source_layer='primary'`）。
3. 売上高・営業利益・経常利益・純利益の 4 項目（`SUMMARY_FIRST_CODES`）に限り、短信 ZIP のサマリー名前空間（`ZIP_SUMMARY_NAMESPACES` ＝ `tse-ed-t` と REIT の `tse-re-t`）に当期実績（`ResultMember`、点）があれば手順 2 の本表値より優先する（`source_layer='summary'`、連結優先・`priority` 順、会社独自タグは対象外、KAB-375）。小売の営業収益と売上高の差、IFRS の 3 か月と累計の取り違え、本表に営業利益が無い IFRS 会社で J-Quants の `statements` と値を揃えるためであり、EDINET にはサマリーが無いため影響しない。
4. 短信 PDF のサマリー（`pdf-sum`）は、本表経路で `net_sales` 実績が 1 件も取れなかったときだけフォールバックとして使い、本表の値は上書きしない（KAB-58・KAB-59）。
5. `eps_basic`・`eps_diluted`・`bps`・`equity_ratio`・`roe`（`SUMMARY_ACTUAL_CODES`）の実績は、本表コンテキストに加えて短信表紙の `ResultMember` を許可する（前期の実績 context は `is_normalizable_context` が落とす）。
6. 配当（`dps`）は `FirstQuarter`・`SecondQuarter`・`ThirdQuarter`・`YearEnd`・`Annual` メンバーで `target_period` を分け、`ResultMember` を実績・`ForecastMember` を予想とする。
7. 有報の追加科目（KAB-78）は `relative_period='current'` の実績点（連結・単体別の行）から採り、マッピングの `part: months` は同一科目・同一 context の年数に `月÷12`（小数第 2 位丸め）を足す。
8. 業績予想は `ForecastMember` またはレンジメンバー（`LowerMember`・`UpperMember`・`MinimumMember`・`MaximumMember`）を持つ fact を対象とし（四半期末と通期予想期末はずれるため `relative_period='current'` は要求しない）、`PreviousMember` があれば `forecast_previous`、無ければ `forecast` とする。`target_period` は `context_ref` の文字列（`accumulatedq1`・`firstquarter` → `Q1`、`interim`・`accumulatedq2`・`secondquarter` → `H1`、`accumulatedq3`・`thirdquarter` → `Q3`、`nextyear`・`currentyear`・`fiscalyear` → `FY`）を優先し、無ければ期間日数（`80〜100` 日 → `Q1`、`150〜220` 日 → `H1`、`240〜300` 日 → `Q3`、`301` 日以上または判定不能 → `FY`）で決める。
9. `is_consolidated` が未判定なら `1` とし、`sign='invert'` なら符号を反転する。`unit` は fact の単位を使い、fact が `pure` または空のときだけ科目の `unit`（`persons`・`years`・`ratio`、KAB-147）、どちらも無ければ `JPY` にする。本表実績で `is_required=1` が欠けたら警告ログを出して処理を継続する。

通常実行は `xbrl_financials` が無い filing と、`parsed_at` が既存の `generated_at` より新しい filing だけを処理する。`--rebuild` は `xbrl_financials`・`xbrl_segments`・大株主の 3 テーブルを全削除（`--code` 指定時は当該銘柄のみ）して全 `parsed` filing を再処理し、削除前後の行数をログに出す（KAB-83）。削除を伴わない部分再実行には `--since YYYY-MM-DD` と `--source {tdnet,tdnet_pdf,edinet}` を使う。

### セグメント注記

有報・半期報・四半期報の報告セグメント情報（売上高・利益・資産）を縦持ちの別テーブル `xbrl_segments` に正規化する（KAB-77、`app/xbrl/segments.py`、`config/xbrl/mappings/segments.yaml`）。

#### 実データの特徴と生データ層・正規化規則

実データで確認した特徴と、生データ層・正規化層の規則は以下のとおりである。

- 2025 年の有報 11 社・半期報 3 社・四半期報 3 社で確認したとおり、数値で取れる軸は `jpcrp_cor:OperatingSegmentsAxis` のみであり、地域別・主要顧客は `1000` 文字超のテキストブロック（`RevenuesFromExternalCustomersInformationForEachRegionTextBlock` 等）しか無いため対象外とする。単一セグメント会社（任天堂・武田）や米国基準（野村）はセグメント数値 fact が `0` 行で正常であり、同一軸上の従業員数等は生 fact に残るが正規化はしない。
- 各セグメントは会社独自メンバー（`jpcrp030000-asr_E02144-000:AutomotiveReportableSegmentMember` など）であり、標準メンバー（`ReportableSegmentsMember` 等）も会社ごとにラベルが変わるため（日立の「小計」「全社及び消去」など）、パーサが書類ごとの日本語ラベルを `xbrl_contexts.member_labels_json` に保存する。EDINET の `keep_fact`（`app/edinet/xbrl.py`）は当期のセグメント軸付き fact と大株主・所有者別軸（`MajorShareholdersAxis`・`MajorShareholdersVotingRightsAxis`・`ClassesOfSharesAxis`、KAB-266）を残す（導入前の過去分は `edinet_xbrl.py --reparse` が必要）。
- 標準科目は `std_items.yaml` の `statement: SEG` 4 項目（`seg_net_sales` 計・`seg_external_sales` 外部顧客への売上高・`seg_profit`・`seg_assets`）である（トヨタ・ソニーのように売上を会社独自要素で出す項目はマッピングしない。セグメント利益は日本基準の `OperatingIncome` のほか、IFRS では `OperatingProfitLossIFRS`・日立の `SegmentProfitLossIFRS`・ソフトバンク G の `ProfitLossBeforeTaxIFRS`・伊藤忠の `ProfitLossAttributableToOwnersOfParentIFRS` に対応する）。
- `normalize_filing` の末尾で `normalize_segments` を呼び、同一 `(科目, 軸, メンバー, 連結, 期末)` では `priority` 昇順、同順位なら期間が長い累計を選び、IFRS 書類では `jppfs_*` を使わない。`member_kind` は標準メンバーを `subtotal`・`total`・`other`・`reconciling` に振り分け、会社独自メンバーは名前に `Elimination`・`Reconcil`・`Adjustment`・`Corporate` があれば `reconciling`、`ReportableSegment` があれば `segment`、それ以外は `unknown` にする。

2025 年 3 月期の有報 6 社における IR Bank `list_segments`（百万円値）との突合結果を下表に示す（同じ 17 件で既存の `xbrl_financials` も `main` と全行一致）。

| 会社（4 桁コード） | 基準 | 外部顧客への売上高 | セグメント利益 | セグメント資産 |
|---|---|---|---|---|
| オリエンタルランド（`4661`） | 日本基準 | 3 セグメント一致 | 一致 | 一致 |
| JR 東日本（`9020`） | 日本基準 | 4 セグメント一致 | 一致 | 一致 |
| 信越化学（`4063`） | 日本基準 | 4 セグメント一致 | 一致 | 開示なし（IR Bank も `null`） |
| 日立（`6501`） | IFRS | 4 セグメント一致 | 一致（調整後営業利益） | 一致 |
| 伊藤忠（`8001`） | IFRS | 8 セグメント一致 | 有報注記どおり（株主帰属当期純利益、IR Bank の営業利益は有報外の値） | 一致 |
| ソフトバンク G（`9984`） | IFRS | 3 セグメント一致 | 有報注記どおり（税引前利益、IR Bank は `null`） | 開示なし |

## 保存

### データベース設計

テーブル・列・索引の定義と ER 図は自動生成の [スキーマ](../schema/xbrl.md) を参照する。XBRL・決算書類ドメインは以下の 16 テーブルと 1 ビューで構成する。

| 層 | テーブル・ビュー | 役割 |
|---|---|---|
| 生データ層 | `xbrl_filings`, `xbrl_elements`, `xbrl_contexts`, `xbrl_facts`, `xbrl_presentation_roles` | 提出書類メタデータ、要素定義、コンテキスト、生 fact、表示リンクの財務 3 表区分を保持する |
| 原文・変換 HTML 層 | `xbrl_documents`, `tdnet_pdf_documents` | 短信 ZIP 内の原文 HTML（gzip）と、IR BANK の過去短信 PDF から変換した SVG ベース HTML（gzip）を保持する |
| 正規化・対応層 | `xbrl_std_items`, `xbrl_element_mappings`, `xbrl_financials`, `xbrl_segments`, `xbrl_pdf_matches`, `xbrl_shareholder_filings`, `xbrl_major_shareholders`, `xbrl_shareholding_by_category` | 標準科目とマッピング、正規化済み財務数値・セグメント・大株主・所有者別状況、および PDF 単独期の要素対応を保持する |
| ジョブ管理・ビュー | `xbrl_job_runs`, `xbrl_statement_values`（VIEW） | ジョブ実行記録と、有報・短信 XBRL・短信 PDF を同一要素キーで横断参照するビューを提供する |

設計意図とスキーマ上の約束は以下のとおりである。

- 年度ごとのタクソノミ改訂に列追加なしで追従できるよう縦持ちとし、マッピングは `element_id` ではなく `namespace_key + local_name` で結ぶ。金額は `Float` にせず `NUMERIC`（`Decimal`）で保持する。
- SQLite を前提としつつ Postgres 互換の型を使い、`JSONB`・`GENERATED ALWAYS`・部分インデックスの `WHERE` は使わない。外部キーは XBRL テーブル間だけに付け、`stocks` への FK は張らない。
- 決算一覧の売上総利益向けにカバリングインデックス `idx_xbrl_financials_gp_cover` と `idx_xbrl_filings_id_cover` を張り、先頭 2 列が重複する旧 `idx_xbrl_financials_lookup` は `ensure_indexes` で外したうえで `ANALYZE` を実行する（KAB-295）。
- `xbrl_filings.source` は `'tdnet'`（`doc_id` は ZIP 名）・`'edinet'`（有報・半期報・四半期報）・`'tdnet_pdf'`（開示番号 14 桁、`raw_path` は `tdnet_pdf:<開示番号>`、KAB-173）の 3 種であり、同じ 14 桁の `'tdnet'` が `parse_status='parsed'` になったら `'tdnet_pdf'` の filing を削除する（`'failed'` や `'no-ixbrl'` の ZIP では削除しない）。

### 短信の原文 HTML

[銘柄](../screen/stock.md) タブで決算短信を PDF と同じ並びで読むため、TDnet 短信 ZIP 内の HTML を gzip 圧縮して `xbrl_documents` に保存する（KAB-161、EDINET は ZIP 構造が異なるため対象外）。数値層とは独立しており、パース・正規化の結果は変えない。

- 2026 年 8 月 10 日〜10 月 23 日の 304 件で確認したとおり、短信 ZIP にはサマリーの `Summary/*-ixbrl.htm` と添付資料全文の `Attachment/qualitative.htm`（EBITDA や定性的セグメント表など数値タグの無い表も含む HTML、REIT は 39 件中 28 件に存在）があり、`5933`（2027 年 3 月期中間）で Chromium PDF 化すると 12 ページ・数字 650 個が元 PDF と一致した（`script` は 0 件、外部画像は `release.tdnet.info/img/mark1.gif` の FASF マークのみ）。
- 抽出順（`app/xbrl/documents.py` の `pick_documents`）は `Summary/` の `*-ixbrl.htm`（名前順）に続けて `qualitative.htm` を 1 つ採り、`qualitative.htm` が無ければ `Attachment/` の `*-ixbrl.htm` を名前順（`0101010-...` が本表順）に採る。フォルダを持たない予想修正（`tse-rvfc...`）などの ZIP は直下の `*-ixbrl.htm` を名前順に `summary` として採り、1 件も無ければ `xbrl_filings.documents_status='empty'`（保存済みは `'stored'`、未処理は `NULL`）にする。頻繁に検索する `xbrl_filings` に BLOB を載せないため別表にしている。
- `xbrl_parse` は各 ZIP のパース直後に原文を作り直し（失敗してもパース結果は変えず `documents failed ...` をログに出す）、実行末尾で原文未作成の TDnet filing（`parse_error='no-ixbrl'` を除く）を新しい順に `XBRL_DOCUMENTS_FILL_LIMIT`（既定 `3000`）件まで埋める（`fill_missing_documents`、`empty` 済みは再読込しない）。
- DB には元の HTML をそのまま保存し、XML 宣言除去・危険要素や `on*` 属性や `javascript:` URL や外部 `http(s)` 画像の除去・`<head>` への `html{-webkit-text-size-adjust:100%;text-size-adjust:100%}` 注入は配信時の `sanitize_html`（`SANITIZE_VERSION = "2"`、ETag は `"{sha256}.s2"`）で行う。

## 過去の短信 PDF

短信 PDF は、短信 XBRL が手に入らない過去の短信を読むための代わりである。新しい短信は短信 XBRL（TDnet の ZIP）と有報・半期報の XBRL（EDINET）から読み、短信 PDF から財務 3 表を新しく読むことはない（新しい短信を PDF で取り込む cron は無く、同じ開示の短信 ZIP を読めば PDF の filing は外す）。短信 PDF の財務 3 表は増えない過去の決まった書類の集まりなので、読み取りの直しは過去の期の表を正しくするためだけに行い、今後の新しい書き方への備え（規則の先回り・監視）は足さない。

### 過去の短信を PDF から HTML にする

TDnet の ZIP は `2026-08-10` 以降しか手元に無く、TDnet 上でも約 1 か月で消える。それ以前の原文は、IR BANK が保管する TDnet 開示 PDF（`https://api.irbank.net/api/market-files/v1/tdnet/{docID}.pdf`、認証なし、`statements.disclosure_no` ＝ TDnet docID ＝ IR BANK `doc_id` の 14 桁番号、TDnet ZIP 名は `0812`＋14 桁）から SVG ベースの HTML に変換して `tdnet_pdf_documents` に入れる（KAB-164・KAB-169）。

#### 変換の仕組み（`app/xbrl/pdf/convert.py`）

PDF から SVG ベース HTML への変換仕様は以下のとおりである。

- KAB-169 で読取器を `pdfplumber`（`"svg1"`）から `pypdfium2`（C++ の pdfium、`"svg2"`）に切り替え、41 件（2021〜2026 年、1 件平均 20 ページ）の変換時間を `126.5` 秒から `16.6` 秒（約 `7.6` 倍）に短縮し、pdfium 文字列比較でも 75 万字中 1 字差（意図的に捨てる `\ufffe`）に抑えている（曲線描画により gzip 後は約 7% 増える。`"svg1"` の既存行は入れ直さない。日本語言語パックの無い poppler `pdftotext` は非埋め込みフォントで `Missing language pack for 'Adobe-Japan1'` を出して文字を落とすため使わず、ロゴ・グラフ等のラスター画像は出さない）。
- pdfium が生成した空白・改行（generated）と制御文字・`\ufffe` を捨て、UTF-16 単位で返るサロゲートペア（`𠮷` など BMP 外）を同一テキストオブジェクト内で 1 文字に復元する。対にならない孤立サロゲートは変換返却直前と `ingest_one` の UTF-8 エンコード直前に `U+FFFD` 1 文字へ置換し、`UnicodeEncodeError` を防ぐ（KAB-169 の `85110` `20260212556179`、KAB-210）。
- ベースライン差が `1pt` 以内の文字を 1 行にまとめ、左から並べて字間が `1.2pt` を超えるか、書式（フォント種別・太字・色）が変わるか、フォントサイズ差が `0.05pt` 以上になれば語を切る。語幅は `textLength`（`lengthAdjust="spacingAndGlyphs"`）で合わせ、フォント名に `gothic`・`sans` 等があれば `sans-serif`（他は `serif`）、太さ `600` 以上または `bold`・`W6`〜`W9` 等があれば太字にする。図形は Form XObject の入れ子（`8` 段まで）の行列を掛け合わせ、白塗り線なしを除き SVG の `<path>` にする。題名は 1 ページ目で `決算短信`・`業績予想`・`お知らせ` 等を含む最初の行とする。
- 文字の大きさは pdfium のフォントサイズに文字の行列（テキスト行列 × CTM）の縦の拡大率を掛けた値にする（`"svg3"`、KAB-572）。`"svg2"` はフォントサイズだけを使っていたので、`1pt` を行列で `11` 倍する PDF（ニッスイ `20250521559959` など）は字が高さ `1pt` の横線になり、ページを `0.75`〜`0.88` 倍に縮める PDF は字が縦に伸びていた。文字・座標・語幅は変わらず、拡大率が語の途中で変わる行だけ語の切れ目が増える。

#### 取込バッチ（`jobs/xbrl/tdnet_pdf_ingest.py`）

取込スクリプトの実行仕様は以下のとおりである。

- `--code`（複数可）または `--all` で指定した `stocks` 登録銘柄の `statements` のうち、`xbrl_documents`（ZIP 名末尾 14 桁）にも `tdnet_pdf_documents` の `'ok'`・`'missing'` にも無い開示を新しい順に処理する（`'failed'` は `--retry-failed` 時のみ対象、VPS で銘柄指定により実行）。
- `--sleep`（既定 `1` 秒）間隔・上限 `--limit` 件で 1 件ずつ取得・変換・コミットする。IR BANK が `429`・`503` を返せば終了コード `0` で停止し、`/Linearized` の `/L` より短い途中切れ PDF（`79520` の `20260212556420` など）は `'failed'`（`truncated pdf`）にする。最終行は `tdnet_pdf stopped=<done|limit|rate_limited> ok=... missing=... failed=... remaining=...` を出力する（`--dry-run` は件数と先頭 10 件のみ）。
- IR BANK の PDF は 1 日 `200` 件まで（`x-ratelimit-limit: 200`、超えると `429` と `retry-after` が約 1 日）。
- `--redo FILE`（開示番号を 1 行 1 つ）は、一覧のうち `converter` が今の版でない回を取り直して上書きする（KAB-572）。取り直しが `404` や失敗でも既存の行は書き換えない。字が横線になった `"svg2"` の回の一覧は `python tools/tdnet_pdf/tiny_text.py --db stock.db`（読むだけ）が出す。語の `textLength` が字数 × 文字の大きさの `3` 倍を超える語が `1%` 以上ある回を出す。縦に伸びた回は HTML からは見分けられないので一覧に入らない。本番の 7,181 件を `config/xbrl/tdnet_pdf_redo.txt` に置き、cron が毎晩 02:20 に `--limit 190` で取り直す（約 38 日。終わったら cron の行と一覧を消す）。

### 短信 PDF の数値

`tdnet_pdf_documents.status='ok'` の HTML から `<text>` 座標を読み取り、ZIP と同じ生データ層・正規化層へ入れる（KAB-173、`app/xbrl/pdf/` の `sync_pdf_filing`、現行 `READER_VERSION = 26`）。`normalize_filing`・`rebuild_all`・決算速報 UI は変えない。

基本仕様と対象フィルタを下表に示す。

| 項目 | 仕様 |
|---|---|
| `source`・`doc_id`・`raw_path` | `xbrl_filings.source='tdnet_pdf'`、`doc_id` は 14 桁の開示番号、`raw_path` は `tdnet_pdf:<開示番号>` とする |
| `raw_sha256`（指紋） | `sha256(html.sha256 + ":" + READER_VERSION + ":" + accounting_standard)` とする |
| 名前空間とマッピング | `pdf-sum`・`pdf-bs`・`pdf-pl`・`pdf-cf`（URI は `http://stock-analyzer.local/taxonomy/tdnet-pdf/`）を使い、`config/xbrl/mappings/tdnet_pdf.yaml` で `label_key` の結果を標準科目へ結ぶ |
| 対象銘柄と対象外開示 | `stocks` に無い銘柄は新規 filing を作らないが（`absent`、KAB-526）、既存の filing は消さずに読み直す。対象 DocType は `{1Q|2Q|3Q|FY}FinancialStatements_{Consolidated|NonConsolidated}_{JP|IFRS}`・`EarnForecastRevision`・`DividendForecastRevision` とし、REIT・米国基準・`4Q`・`5Q`・日付欠損・1 ページ目題名に「速報値」がある開示（`absent`・`flash-results`）・`config/xbrl/pdf_other_standard.json` にある別基準財務諸表の追加開示（`2130`・`436A`、`absent`・`other-standard`）は作らない（1Q・3Q の CF 欠落は失敗にしない） |
| 訂正・基準補正・連結判定 | 題名の「訂正」「決算短信…の修正について」「の一部修正」または本文の「（訂正前）」「（訂正後）」「（訂正版）」「<変更前>」「<変更後>」で `is_amendment=True` とし、「（訂正前）」から「（訂正後）」の手前までを捨てる（KAB-212・KAB-226）。BS の本表に限り、山括弧の「〈訂正前〉」「〈訂正後〉」「<訂正前>」も同じに扱い、一部訂正の表の「（中略）」の行は読まない（`6137` `20250910555890` で訂正前と訂正後の流動資産がどちらも入り、「(中略)資産合計」になっていた、KAB-593。サマリー・PL・CF は今の括弧のまま）。J-Quants DocType の基準誤りは `config/xbrl/pdf_standard_fixes.json` の `jquants` と一致するときだけ `standard` に直し（不一致は `WARNING tdnet_pdf standard-fix-mismatch`、未登録の題名〔IFRS〕・〔日本基準〕不一致は `WARNING tdnet_pdf standard-candidate`、KAB-516）、修正開示の会計基準と `EarnForecastRevision` の連結区分（見出しに「個別」「単体」も「連結」も無い場合、`7865` の 1Q・3Q 修正など、KAB-247・`READER_VERSION=9`）は同一銘柄の日付が近い通常短信から引き継ぐ |

#### 表・列見出し・金額・行名の座標読み取り規則

PDF 変換済み HTML から表・列・金額・行名を抽出する規則を下表にまとめる。

| 工程 | 規則 |
|---|---|
| 柱・ページ番号の除去 | 上下帯（上 `60pt`・下 `780pt`）の外にあるページ番号（`- 6 -`、表中の `- 104` は残す、KAB-220）と、上下帯で 3 ページ以上に `3pt` 以内の同じ高さで出る柱（2 行柱も含む）を先に落とす |
| 列見出しの切り出しと語割り当て | 最初の数値行の直上に続く行だけを列見出しとし、`。` を含む行・`(注)` や `※` や `＝` で始まる行・空白を除き `40` 字を超える語（行全体ではなく語単位で判定し武田の EPS 見出しを通す、KAB-216・KAB-224）・式の注記（`＝` を含む無数値行や `－` の後ろに文字が続く折り返し、`9983` `20251007569462`）・左が「前回・今回・増減・前期実績」で右が円銭等の単位だけの行（`6268` 個別、KAB-243）・右上注記で切る。数値列幅に重なる語はその列に載せ（端だけ接する隣列には載せない、KAB-213）、％列を挟んで金額列 2 つ以上にまたがる語（`6268` の NP＋EPS 見出し、KAB-238）は `textLength` を字数で割って文字ごとに最寄り金額列へ分ける（隣接金額列の「年間配当金」や `1377` の金額＋％列、`7865` の左伸び見出し、第1四半期・通期・業績予想を含む語は分割せず金額列に載せ、％単独列は分け先にしない）。列に重ならない 1 字は単位（`円`・`銭`・`%`）を除き右隣の金額列へ付け（`9501`、KAB-236）、それ以外の重ならない語は中心 `80pt` 以内の最寄り列へ付ける |
| 金額・単位・配当・列統合 | 金額は直前が行頭・空白・`△`・`▲`・`～` のときだけ取り、後ろに空白がある `※`・`※n`（`※1,※2` のような読点区切りの並びも、KAB-544）は消してから取る（KAB-214・KAB-216）。BS の本表に限り、金額の列の右端にそろう `※ 8`・`※ △44`（1 語でも、`※` と数字が別の語でも。同じ表のほかの金額 2 つ以上と右端が `3pt` 以内）は注記の印の付いた金額として読む（`9854`・`3386` の受取手形、`2337` の繰延ヘッジ損益。印ごと消して行名が次の行とくっつき落ちていた、KAB-593）。百万円は `10^6` 倍、億円は `10^8` 倍（`8630` `20231206599881`）、百分率は `pure`（`39.1` → `0.391`）、設備投資の `△` は負のまま入れて `std_items` の `sign: invert` で正にし（サマリーの「設備投資額」はマッピングしない）、IFRS 本表の売上原価・販管費は正値、IFRS 会社の個別表（`#nc`）は JPGAAP として経常利益を採る（KAB-243）。配当の「40 円」の円は飾り、「20円00銭」・1 語の「801円98銭」・隣接する円列＋銭列（`3608`・`3944`・`7590`、`9507` の未定列揃え、`7865` `20241126529281` の未定なし桁違いは束ねない）は 1 つの 1 株当たり値にする（KAB-213・KAB-233・KAB-237・KAB-251）。「未定」と `―`・`－`・`-`・`−`・`—` だけのセルは欠損とし（BS の本表に限り、罫線の `─`・ハイフンの `‐` だけの語も欠損とする。読めず前後の行がくっついて落ちていた `2196`・`7217`・`7571` など、KAB-548）、3 行のレンジ予想（`30pt` 以内の `～` 等）は下限・上限に合成する。`△` だけ数 pt ずれる列（JT）は同行に並ばない `8pt` 以内の列を統合し、またがり見出しと単位語を除き見出し幅内の数値を最大 `x1` へ寄せてから列を決める（KAB-229・KAB-236） |
| 折り返し行名の結合 | 数値の上下にずれた行名や折り返し（`(予想)`・`額（△は減少）`・`増減額（△は減少）`、ただし前回・今回・増減・前期で始まる行は新規行名、空白区切りの `－` だけの未定行は残す、`9501`、KAB-236・KAB-237）を結合し（ただし BS の本表では、行名の無い数値行の直後に出る行名だけの行は、次の数値行より数値行の方に `1.5` 倍以上近ければその数値行の行名にする。改ページの頭で数値が行名より上に出た `6618` `20230530587868` の「土地」が次の「リース資産」とくっつかないように、KAB-548）、予想修正では Forecast 行だけを残し、`pdf-sum` 先頭の「連結」「個別」は落とす（KAB-213） |
| 列の期間・役割判定 | 日付（「至 2026年6月30日」）→ 年月・和暦（分かれた語の結合対応、`令和` 無しの 1〜2 桁年は令和換算と西暦下 2 桁［`6268` の `22年12月期`］のうち年度末に近い方、年シフトは `_tanshin._shift_years` でうるう年の月末を維持［`6506` の `2024-02-29`］、サマリーの末尾「末」でない「第2四半期」は累計 `Q2`、KAB-215・KAB-228・KAB-239・KAB-241・`READER_VERSION=7`）で決める。予想修正（`fc_rev`）は列・行に期が無ければ表見出しの日付範囲（月数 3・6・9・12、20 日決算も同じ）→ 期間語 → 題名で決め、累計と通期が併記されて決まらなければ入れない（`7513`・`7865`）。本表の増減・比率・3 か月単独・予想列は捨てる。BS で 1 つの列の見出しに前期末と当期末の日付が両方載ったとき（訂正短信で見出しが左へずれた `6618` `20230530587868`、KAB-545）は、見出しを使わず位置（右端が当期、ほかは前期）で決める |
| 表境界・配当・`label_key`・異常ページ | 本表は BS・PL（純損益計算書を含み、包括利益計算書は損益計算書が無い書類のみ、KAB-259）・CF の各先頭 1 表（先頭の `①`〜`⑳`・`(1)`・`1.` は NFKC 前に除去、連結短信は「連結」付き見出しのみ、他の計算書・明細書や注記で終端、KAB-234。ページの上帯〔上 `60pt`〕に出る本表の見出しが直前の見出しと同じ表・同じ文字なら、柱として表を切らない〔`9444` `20251030583722` の 2 ページ目の負債の部、KAB-545〕。BS は「負債純資産合計」「負債・資本計」などの総合計の行で終わり、同じ表で後ろに続く注記の行〔`6503` の「社債、借入金及びリース負債残高」「うちリース負債除く残高」〕は読まない、KAB-548）とし、サマリーは最初のサマリー見出しページから 2 ページ以内とする（個別財政状態・個別業績は単体、期不明の `(金融を除く)` 等や同表同科目の異値重複は除外、KAB-226・KAB-235）。配当は `_NonConsolidated`（`is_consolidated=0`）とし、時点だけの列（語分割された「第 2 四半期末」を含む、`9983` `20221215579485`・`8511` `20260212556179`）・`年間配当金（円）` 等の括弧付き（KAB-245・`READER_VERSION=8`）・`年間配当金期末`（`9507`、KAB-251）・予定配当額や同時修正内の配当ブロック（KAB-231・KAB-237）も 1 株当たりとして読み、CF の期首・期末残高は時点（`period_start` 空、KAB-263）にする。行名は `label_key` で正規化し（「又は…損失」除去［「の帰属」は残す、KAB-240］・`(△...)` 除去・先頭 `(内訳)` 除去［KAB-260］・損益→利益・末尾 `(A)` `(B)` 除去・四半期や中間→当期・「X利益の帰属 親会社の所有者」変換・営業 CF 表記統一・四半期末残高→期末残高・1株当り→1株当たり［KAB-251］、無数値の区分見出し（有形固定資産・無形固定資産・投資その他の資産・繰延資産・特別法上の準備金・その他の包括利益累計額・評価・換算差額等も。次の行が「合計」「小計」「計」「累計額」で始まる折り返しなら残す、KAB-544・`READER_VERSION=23`。BS の本表に限り、IFRS の「負債及び資本」「親会社の所有者に帰属する持分」、個別の「資本剰余金」「利益剰余金」「その他利益剰余金」、括弧付きの「(資産の部)」「(負債及び資本の部)」も、KAB-548。サマリー・PL・CF の読み方は変えない・`READER_VERSION=25`）や後続が「に帰属する」の「…の帰属」［`81330`、KAB-264］や IFRS PL の無数値「収益」［KAB-261］は除去、注記番号付き「その他収益5,7」は除外し「1年内…」は残す（BS の本表で列見出しに「注記」のある表に限り、行名の後ろに空白を置いて注記の列に出る「7,15」「7、13」は行名から落として行を残す。`7733`・`285A` の現金及び現金同等物・社債及び借入金・その他の金融負債が落ちていた、KAB-593・`READER_VERSION=26`）。BS の本表に限り、行名の途中の期間・条番号［「預入期間が３ヶ月超の定期預金」「長期債務（1年以内返済予定分を除く）」「保険業法第113条繰延資産」］は残す、先頭の「注1.」は落とす［`6201`・`8031`・`2453`・`6503`、KAB-548］）、「金銭」を含まない「銭」「円銭」見出し（KAB-452・`READER_VERSION=22`）・裸の「円」列・「株当」行と直下の「基本的」「希薄化後」等の行（KAB-432）を 1 株当たり（`100,000` 以上は除外）とする。文字化けページや図形 `80` 個以上で文字 `30` 字未満のアウトライン化ページは `garbled pages=...`、2 ページ目以降の無文字ページは `textless pages=...` を `parse_error` に付けて `'parsed'` のまま残す（表が 0 個のときのみ `'failed'`、KAB-218・KAB-227・KAB-262） |

#### 突合検証と一括実行（`verify_financials.py`・`financials.py`）

TDnet ZIP との突合（2026 年 10 月 26 日、`tools/tdnet_pdf/verify_financials.py`、スクラッチ SQLite 上で実行し J-Quants 日付が揃う `statements` は ZIP 期末で上書きしない、KAB-219）の結果を下表に示す。

| 検証セット | 一致 | 不一致 | ZIP のみ | PDF のみ |
|---|---|---|---|---|
| 開発に使用した 85 組（日本基準の連結・非連結、IFRS 22 組） | `2,817` | `0` | `63`（うち 1Q・3Q の CF `14` 件は `expected_zip_only`） | `17` |
| 未使用の 57 組（日本基準） | `1,886` | `0` | `122` | `41` |
| 未使用の 30 組（IFRS） | `909` | `0` | `54` | `9` |

「ZIP のみ」「PDF のみ」は減価償却費・設備投資・研究開発費の有無や決算期変更（`37500`）によるものであり、不一致はすべて `0` である。J-Quants（40 社余り 177 件）とは `2,176` 件一致・`1` 件差（ソニーの CF 期末現金と BS 現金の差）、EDINET 有報・半期報（43 銘柄・80 期）とは `1,647` 件一致・`16` 件差（武田の短信出し直し、`26740` の BS 改訂、イオン・SOMPO の売上定義差、東京海上の単体日本基準と連結 IFRS の基準差、`44930` の ROE、ソニーの現金）であった。

`tools/tdnet_pdf/financials.py` は新しい順に最大 `--limit`（既定 `2000`、全件は `100000`）件を処理する（cron には載せず手動実行、同一指紋は `unchanged` でスキップ、`--reparse` で強制再読込）。`--batch N` 指定時は `N` 件ごとに進捗行を出し、`database is locked` を区切り末尾で `5` 秒待って 1 回再試行する（KAB-435、1 件ごとコミットのため中断再開可）。末尾で変更銘柄の `xbrl_pdf_matches` を付け直す（`--no-matches` で省略可）。

### 短信 PDF の回帰チェック

`app/xbrl/pdf/`・`config/xbrl/mappings/tdnet_pdf.yaml`・`tools/tdnet_pdf/financials.py` を変更する PR は、`tools/tdnet_pdf/golden_check.py` を通して実データの退行を止める（KAB-244、合成テストをすり抜けて J-Quants 一致数を `1,795` から `1,565` に落とした #1071 を契機に導入）。

- 正解データ `tests/golden/tdnet_pdf/expected.json` は検証 bot 2〜3 回目（19 銘柄・407 開示、2021 年 10 月より前の 12 開示は J-Quants Light 対象外のため除外）から J-Quants 一致値（`values`、`evidence` に J-Quants 項目名、将来用の `"edinet:..."` 形式にも対応）と不一致項目（`known_gaps`）を収録する。
- 全件 HTML は `data/tdnet_pdf_golden/html/{開示番号}.html.gz` と `titles.json`（リポジトリ外、`fetch` が IR BANK PDF を `convert.py` で変換）に置き、原因別の 38 開示（`tests/golden/tdnet_pdf/html/` と `classes.json`、会計基準引き継ぎ用の直前短信を含む）は CI の `tests/xbrl/test_tdnet_pdf_golden.py` で毎回回す。
- 一時 SQLite 上で開示番号順に `sync_pdf_filing` を流し、正解値が消えるか変われば終了コード `1` にする（J-Quants 側の誤り等で意図して変える場合のみ PR 本文に理由を書いて `expected.json` を同時更新する）。なお `CurPerType` が `1Q`・`3Q` の予想修正で J-Quants が末尾なし欄（`FSales`・`FNCSales` 等）に返す累計予想は、`jquants_checks` が通期ではなく `CurPerEn` の `Q1`・`Q3` 累計として突き合わせる（`2Q` 修正は末尾なしが通期・`2Q` 欄が上期、`CurPerType` 不明の末尾なし欄は対象外、KAB-246）。

```bash
python tools/tdnet_pdf/golden_check.py fetch
python tools/tdnet_pdf/golden_check.py check --base-repo /tmp/sa-main --md /tmp/golden.md
python tools/tdnet_pdf/golden_check.py build --statements fins_summary.json \
  --cmp-jq r2_prev5=.../cmp_jq_prev5.json --cmp-jq r2_new5=.../cmp_jq_new5.json --cmp-jq r3=.../cmp_jq.json
```

## 使う所

### statements との関係

`statements` テーブルと `xbrl_financials` テーブルの役割分担を下表に示す。

| 観点 | `statements` | `xbrl_financials` |
|---|---|---|
| データ源 | J-Quants 短信サマリ（`source='jquants'`、KAB-383 以前は `NULL`）と、短信 XBRL から生成した行（`source='xbrl'`） | TDnet XBRL・EDINET XBRL・過去短信 PDF の生 fact を正規化した行 |
| スキーマ形状 | 売上・営業利益・予想などを固定列で持つ横持ち | 標準勘定科目×値の縦持ち |
| 画面・API の参照 | 決算速報・銘柄詳細サマリの正本 | 決算表の修正履歴のみデータ源として選択可能（既定は `statements`） |
| 対象期間・項目 | 累計実績と会社予想 | 本表の当期実績に加え、EPS・配当・会社予想（通期・中間・レンジ・修正前）・有報項目 |

#### 短信 XBRL から `statements` 行を作る規則（KAB-383）

担当ジョブは [`xbrl/xbrl_statements`](gen/xbrl.md#xbrlxbrl_statements)（`jobs/xbrl/xbrl_statements.py`、`app/xbrl/statements.py`）である。J-Quants の取込は 19:30 の 1 回だけであるため、毎時入る TDnet の解析済み filing（決算短信・業績予想修正・配当予想修正）から開示番号（`doc_id` 末尾 14 桁、2026 年 10 月以降の `1,829` 件すべてで J-Quants の `disclosure_no`・開示日時と一致）をキーに `statements` 行を生成する。

- 数値は XBRL を優先し、XBRL に値がある列を上書きして無い列は既存の J-Quants 値を残す（`source='xbrl'`）。後の J-Quants 取込は `source='xbrl'` 行の数値・日付を変えず、`raw_json` を J-Quants のものに差し替えて空の数値列だけを埋める。
- 実績は `normalize` の当期実績（連結優先）、期首は累計実績の期首、年度末は同一期首の通期予想期末（無ければ期首から 1 年）とする。本決算以外の `fy_*`・`f2_*` には当期の通期・中間予想を入れ、本決算では空にして来期予想を `raw_json` の `NxF*`・`NxF*2Q`・`NxtFYEn`（無ければ年度末の 1 年後）に入れる。業績予想修正は中間予想があり配当予想が無ければ `type_of_period='2Q'`、それ以外は `'FY'` とする（2026 年 8 月以降の `237` 件中 `217` 件で J-Quants と一致）。
- J-Quants の `raw_json` が無い間は `F*`・`NxF*`・配当 `Div*` `FDiv*` `NxFDiv*`（単体 context を参照）・`BPS`・`EqAR`・`ROE` と `"Source": "xbrl"` を作り、`type_of_document` も J-Quants 形式（`2QFinancialStatements_Consolidated_IFRS`・`EarnForecastRevision` など）にする。
- 単体のみの個別業績予想修正は `raw_json` の `FNC*` に入れて `fy_*`・`f2_*` を空にするが、直近の決算短信が単体の会社に限り `fy_*`・`f2_*` にも入れる。REIT（題名に `REIT`）と実績の無い「決算速報値のお知らせ」（KAB-222）は生成せず、J-Quants 行との突合は `tools/xbrl/xbrl_statements_check.py --db ... --since ...` で行う。
- 予想の取り下げ（KAB-551）: 業績予想・配当予想の修正と、`doc_type='other'` の書類（取り下げのお知らせ・連結決算への移行に伴う公表など）について、項目ごとに修正前（`value_kind='forecast_previous'`）の行があり修正後（`'forecast'`）の行が無ければ取り下げとして `statements.withdrawn_forecasts` に入れる。値が nil の項目は `xbrl_financials` に行ができないので、修正後が未定なら修正後の行が無くなる。行の有無は `range_side` を問わない（点からレンジに変えた修正は取り下げではない）。業績は通期（`target_period='FY'`）の売上高・営業利益・経常利益・純利益・EPS、配当は年間（`'annual'`、単体の context）を見て、年間の修正前があり修正後が無いことに加えて期末（`'year_end'`）の修正後も無いときだけ印を付ける。株式分割に伴う配当予想の修正は、分割の前後を単純に足せないので年間の修正後が「－」で行が無く、期末の修正後（分割後の金額）だけがあるため、取り下げではない。業績は連結の行があれば連結（修正前の行も含めて判定）、無ければ個別を見る。連結の会社が個別の予想だけを取り下げた場合（上の `FNC*` に分ける場合と同じ条件）は印を付けない。直近の決算短信が単体の会社で修正後の連結の予想が無い「連結決算への移行」は、単体の予想の取り下げとして印を付ける。`doc_type='other'` の書類からは行を作らず、同じ開示番号の `statements` 行（J-Quants）が既にあるときだけ印を付ける。判定は `app/xbrl/statements.py` の `withdrawn_forecasts`。

### 財務3表

財務 3 表を出どころ横断で並べる API は `GET /api/stock/{code}/financials`（実装は `app/xbrl/three_statements.py`、MCP `financial_statements` はその TSV 変換）であり、応答仕様は [銘柄](../screen/stock.md) に記載する。専用テーブルやバッチは足さず、リクエストごとに 1 銘柄の `xbrl_facts` から組み立てる。

#### 材料と連結・個別の統合

読み取る fact の範囲と連結・単体の統合規則は以下のとおりである。

- 対象は `source` が `'edinet'`・`'tdnet'`・`'tdnet_pdf'`、`doc_type` が `'annual'`・`'semiannual'`・`'quarterly'` の `'parsed'` filing であり、当期（`relative_period='current'`）・連結または単体メンバーのみ・単位が円（`None`・`'JPY'`）または 1 株当たり円（`'JPYPerShares'`）の数値 fact を読む。`jpdei_cor`・`jpcrp_cor`・`tse-ed-t`・`tse-re-t`・`pdf-sum` は除外し、XBRL 側は `jppfs_cor`・`jpigp_cor` と会社独自要素（`is_custom`）だけを出す。
- 同一 filing・同一連結区分・同一要素に当期の期間 fact が複数ある場合（累計と 3 か月が並ぶ報告書・短信）は、`start_date` が最も早い期首からの累計だけを使う（表の振り分け自体は捨てる前の並びで行う）。有報で DEI が「連結財務諸表を作成していない」とある書類はすべて個別とする。
- `consolidated=auto`（既定）は、連結 fact でいずれかの表が組める filing が 1 つでもあれば連結を主にし、連結の最古期より前と最新期より後の期だけを個別の列で埋める（途中の期は個別で埋めず、PDF の対応付けも連結・個別それぞれの内部だけで行い、科目名は連結側を優先する）。

#### 表の振り分け

PDF は名前空間（`pdf-bs`・`pdf-pl`・`pdf-cf`）で分け、XBRL は `presentation_status='ok'` なら表示リンクの定義で、それ以外（`NULL`・`'none'`・`'gone'`・`'failed'`）は印字順（fact の `id` 順）からの推定で分ける。

- 定義で分ける場合は、`xbrl_presentation_roles` を連結区分・表ごとに束ね、日本基準と IFRS の両方の区分があれば書類の `accounting_standard` に合う方だけを使う（片方のみならそれを使う）。PL は損益計算書と包括利益計算書を `seq` 順につなぐが、`SingleStatement` を含まない単独の `StatementOfComprehensiveIncome` は損益計算書の後ろに置く。行順は印字順と定義順の最長一致列を本体としてその範囲内は印字順を保ち（合計が内訳より前にある `4052`・`4483`・`5020` 対応）、範囲外の fact（前の表で先に印字された純資産合計や税引前利益、CF の期末現金などの時点 fact）は定義の位置（当期の時点 fact は `periodEndLabel`、無ければ `periodStartLabel` でない最後の出現、`4568` 対応）へ差し込む（投資法人の PL の時点要素なども絞らず、定義に無い表は推定で補わない）。
- 推定で分ける場合は、BS を合計要素（`Assets`・`LiabilitiesAndNetAssets`・`*IFRS` 等）を含む最長の時点 fact 列、CF を `...OpeCF`・`...InvCF`・`...FinCF`・`NetCashProvidedByUsedIn...`・`NetIncreaseDecreaseInCashAndCashEquivalents...`・`EffectOfExchangeRateChange...`（`NetCashProvidedByUsedIn...` が無ければ CF なし）と直後の現金増減（`*CCE`・`*InCashAndCashEquivalents*`、ただし `CashAndCashEquivalentsInterestIncomeIFRS` のように `CashAndCashEquivalents` で始まる期間要素は除く）および期末残高 `CashAndCashEquivalents...` までとする。PL は BS の後ろ・CF の前の期間 fact のうち最後の `ProfitLoss`・`NetIncome`・`ComprehensiveIncome`・`...PerShare` までとし、海運や武田などのように BS より前の期間 fact の方が目印要素を多く含む書類では、先頭の経営指標等（`...SummaryOfBusinessResults`・`...KeyFinancialData`）と政策保有株式（`...InvestmentEquitySecurities...`）を除いたうえでそちらを PL にする。

#### 行のキーと PDF 行・XBRL 要素の対応付け

行キーは標準要素が `local_name`、会社独自要素が `ext:<local_name>`、未対応の PDF 行が `pdf:<local_name>` である。

同じ期末に PDF（最新開示と同一基準で最大行数の 7 割以上ある最新開示）と XBRL（有報 ＞ 短信 XBRL の新しい方）が揃う回で、表ごとに非ゼロ絶対値の一致（丸め粒度差は許容）による LCS（同長なら土台・語幹・正規化名と要素ラベルの一致数が多い方）を取る。LCS で付かない行も、その表で値が 1 回・XBRL 全体で 1 要素・粒度の `100` 倍以上なら表をまたいで付けるが、PL・CF の行をその表外の時点要素に付けること（`8179` の CF 期首残高→流動負債合計の誤対応防止、`CashAndCashEquivalents*` は例外）と、PL の行を CF 目印要素に付けること（`9206` の事業費明細書→CF 減価償却費の誤対応防止）と、BS の行を、表示リンクのある書類でその BS の表示リンクに無い要素（注記の fact。`4463` の 2023/12 の PDF の流動負債の「その他」→収益認識注記の `NotesReceivableTrade` が値でたまたま一致し、追加要素として毎年の有報の BS に足されていた。KAB-549）や、その表外の期間要素に付けること（`7443` の 2024/3 の PDF の繰延税金資産→PL の法人税等調整額 `IncomeTaxesDeferred` の誤対応防止。付くとその要素が BS の流動資産の頭に出る。`2437` の 2025/5 の PDF の1年内返済予定の長期借入金→CF の `RepaymentOfLongTermLoansPayableFinCF` も同じ）は禁止する。値で付いて XBRL の表の外にあった要素（追加要素）は、ほかの期の XBRL の列にもその表の要素として足すが、表示リンクのある書類の BS には、その表示リンクに無い要素は足さない（`5233` 個別の 2020/3 の有報に、注記の `ProvisionForLossOnBusinessOfSubsidiariesAndAffiliatesNCL` が足されていた。KAB-549）。符号反転があれば向きも記憶し、値で付いた行は（区分, 土台）でも記憶する（ここでの区分は並びで直後に来る合計要素から決める：BS は大区分・区分合計、CF は営業・投資・財務合計、日本基準 PL は `GrossProfit`・`OperatingIncome`・`NonOperatingIncome`・`NonOperatingExpenses`・`ExtraordinaryIncome`・`ExtraordinaryLoss`）。

#### PDF 行名の土台と 2 回目の印字

PDF の `local_name` から `#n`、先頭に付いた区分見出し（BS の流動資産・有形固定資産・無形固定資産・投資その他の資産・繰延資産・非流動資産・流動負債・固定負債・非流動負債・特別法上の準備金・負債及び資本・負債純資産・株主資本・その他の包括利益累計額・評価換算差額等、PL の販管費・営業外収益・営業外費用・特別利益・特別損失・その他の包括利益と `基本的1株当たり...`・`希薄化後1株当たり...` 直前の 1 株当たり利益見出し、CF の営業・投資・財務 CF。ただし直後が「合計」「小計」「累計額」「計」「又は」「#」・括弧・読点・罫線・「のれん」以外の助詞・「及び」「並びに」・単独の「等」であるものは外さない）、および NFKC 後の「又は…（△）」「（△…）」「利益(損失)」「損失(利益)」・末尾罫線（`─`・`―`）を除いて「土台」を作り、同一表の同一土台に `#n` を振り直す。

PL で同一土台・同一非ゼロ値（「その他」を除く）が 2 回出て片方だけが値で要素に付いた名前（または併用回が無くても名前索引で 1 要素に決まる `当期利益`・`当期純利益`・`当期損失`・`当期純損失`）は「2 回印字される名前」として記憶し、同値の 2 回目以降を前の行の写しとして画面では非表示にし、`xbrl_pdf_matches` には同じ `element_key` を入れる（BS・CF では写しにしない）。

#### 益と損で行名が変わる科目

XBRL ラベル（NFKC 後）が `…利益又は…損失`・`…利益（△損失）`・`…利益（△は損失）`・`…利益（損失）` である符号付き利益要素、ならびに CF の `…の(純)増減額（△は減少）` に対する `…の減少額` および CF の税引前純損失については、併用回に値で付いた行名とラベル末尾の「利益」を「損失」に替えた名前を記憶し、PDF 単独期で 1 行のみかつ値が `0` 以下の行に同じ要素・同じ向きで当てる（`matched_by='value'`、別要素の為替差損や正値の減損損失には作らない）。

#### PDF だけの期における 4 段階の当てはめ

PDF が主となる列（基準変更会社は同一基準の学習結果と XBRL のみを使用）では次の 4 段階で当てる。

1. 第 1 段（値学習の適用）では、「通期どうし・四半期どうし」優先・期末近傍順で土台（`#n` 付き）または区分内で 1 行のみの（区分, 土台）を引き、競合時は別回の未使用要素への移動や末尾合計行（`法人税等` と `法人税等合計` など）への集約を試み、全回順・通期四半期別順・近傍優先順の 3 つの LIS のいずれかに残る対応を採用する。
2. 第 2 段（名前照合）では、未対応行を同一会社・同一表の XBRL `label_ja`（単位種別一致・期末非重複の最新要素を含む索引、期首残高ラベルを持つ `CashAndCashEquivalentsIFRS` は除外、KAB-452）と完全一致または正規化名（`(円)`・`(銭)`・`(注...)` 除去、四半期・中間→当期、`当期包括利益`→`包括利益`、税引前表記の統一）で照合し、表内で複数ある場合は「その他」を除き（区分, 土台）と（区分, ラベル）の一致で当てる。
3. 第 3 段（印字順脱落行の区分内再確認、KAB-451）では、前 2 段で印字順安全弁により落ちた行（営業外内の並び入れ替えや流動・固定間の移動）を、区分内で 1 行のみのときに（区分, 土台）の値学習対応または区分内の名前一致（「その他」除外・単位一致）で救済する。
4. 第 4 段（合計行の語幹照合、KAB-451）では、未対応の合計行から末尾の「合計」「計」を 1 回落とした 2 文字以上の語幹（「その他」や「累計」「会計」「設計」「統計」等を除く）を XBRL ラベルと照合し、`...IFRS` 要素数の多数派基準と一致する候補だけを置いて区分を組み直し、第 1〜第 3 段を再実行して初回未対応行への追加分だけを反映する。

#### 全社の標準ラベルの辞書での当てはめ（第 5 段）

上の 4 段で付かなかった行（その会社の XBRL に一度も出ていない標準の要素の科目: 新規上場、PDF しか読めていない期の新しい科目）を、全社の XBRL から作った辞書で当てる。最後の手段で、上の 4 段で付いた行は変えない。辞書は `config/xbrl/standard_labels.json`（パスは `app/config.py` の `XBRL_STANDARD_LABELS_PATH`）。無い・空なら第 5 段は何もしない。**作り方**（`tools/xbrl/label_dictionary.py --db <DB> --out <JSON>`。DB は読み取り専用で開く）: `xbrl_filings` に `edinet` / `tdnet` の `parsed` がある全銘柄の、「財務3表」の材料（`load_both`）の XBRL の表を読む。会社独自の要素（`ext:`）は使わない。表の会計基準は、その表の標準の要素のうち名前が `…IFRS` のものが多ければ IFRS、少なければ日本基準（`JPGAAP`）、同数なら使わない（書類の `accounting_standard` でなく要素で決める。IFRS の会社の有報の個別の表は日本基準の要素）。名前は `label_ja` に PDF の行名の土台と同じ正規化（`#n` を落とし、`strip_heads`、`_clean_label`、`_canon_name`）をかけたもの。「その他」・まとめない行名（`_never_merged`: 「貸倒引当金」「減価償却累計額…」）・CF の期首残高（`OPENING_CASH_RE`）は載せない。`xbrl_elements` の `label_ja` はタクソノミの版ごとの行で、版によってはほかの要素のラベルが入っている（`RepaymentOfLongTermLoansPayableFinCF` に「長期借入れによる収入」）。要素ごとに版の間でいちばん多いラベルを決め、それと違い、しかもほかの要素のいちばん多いラベルと同じラベルの fact は数えない。区分は「区分の中の対応」と同じ（その要素より後ろで最初の区分の合計の要素。後ろに無ければ区分の外 `-`）。（会計基準、表、名前）ごとに、区分ごとと区分を問わないものの 2 通りで、使っている会社の数を要素ごとに数え、いちばん多い要素の会社数が `MIN_CODES`（5）以上で、全体（要素ごとの会社数の和）の `MIN_SHARE`（95%）以上なら載せる（区分ごと `within`、区分を問わない `any`）。`any` には、その要素がその名前で出る区分（95% 以上が 1 つの区分なら）を `home` として持つ。同じ名前が区分で別の要素になる科目（「リース債務」「資産除去債務」の流動と固定）は `within` だけに載る。単位の種類（1 株当たりか金額か）が会社で揃わない要素は載せない。1 株当たりの要素は `per_share` に並べる。出力は並びを決めて書くので、同じ DB から作り直せば差分は出ない。**当て方**: その PDF の `accounting_standard`（`IFRS` / `JPGAAP`）の辞書だけを引く。基準が分からない PDF には当てない。財務3表では PDF が主の列の PDF にだけ当てる（XBRL が主の列の PDF と、訂正の PDF の値の差し替えには当てない）。`xbrl_pdf_matches` は対象の PDF すべてに当てる。対象は上の 4 段で付かなかった行。土台の名前で引き、無ければ末尾の「合計」「計」を落とした語幹（第 4 段と同じ）で引く（「資産合計」→ ラベル「資産」→ `Assets`）。まず、引いた `any` が区分の合計の要素（`MATCH_MARKERS`・CF の区分の合計）の行を当てる。次に、付いた行（上の 4 段とこの合計）から PDF の各行の区分を決め、区分が決まった行は、その区分の `within`、無ければその区分が `home` の `any` を当てる。表に区分の合計が 1 つも付いていなければ区分を問わず `any` を当てる。表のどこかに区分の合計が付いていて、その行より後ろに無ければ区分の外（`-`）として引く。その表でほかの行に付いている要素と、単位の種類が合わない要素は当てない。この段で 2 行が同じ要素になれば両方外す。向きは PDF の値のまま、`matched_by` は `standard`。この段の行は印字順の安全弁にかけない（上の 4 段の結果を変えないため）。

**名前で引けない行（KAB-535）**: 上の当て方で付いた行（上の 4 段とこの段）を区分と土台にして、まだ付いていない行を次の順に当てる。どれもその表でほかの行に付いている要素・単位の種類が合わない要素は当てず、同じ回で 2 行が同じ要素になれば両方外す（上の当て方の結果は変えない）。

- **作り方の追加**: 辞書に `others`（会計基準 → 表 → 置き場 → 要素）と `accumulated`（会計基準 → 行名 → 1 つ上の行の要素 → 要素）を持つ。無い辞書（KAB-531 の形）は空として読む。どちらも `ext:` は使わず、会社数の下限と割合の下限は `labels` と同じ（`MIN_CODES` / `MIN_SHARE`）。置き場は区分（上と同じ決め方。区分の外は載せない）で、CF の区分の中で `Subtotal…` の要素より前は `<区分>:小計まで`、すぐ下の行が累計額の行なら `<置き場>:累計額の前`（有形固定資産の「その他」の取得価額 `OtherPPE` と純額 `OtherNetPPE` を分ける）。同じ表の同じ置き場に「その他」が 2 行あればその表は数えない。ラベルの版の食い違いは数えない、の規則は「その他」と累計額には使わない（どちらも多くの要素がいちばん多いラベルを共有するため）。累計額は、ラベルが「減価償却累計額」「減損損失累計額」「減価償却累計額及び減損損失累計額」の要素を、間の累計額の行を飛ばした 1 つ上の要素で数える。すぐ下（間の累計額の行を飛ばす）の行が「…（純額）」の時だけ数える（有形固定資産の合計の直前でまとめて引く `AccumulatedDepreciationPPEByGroup` を混ぜないため）。
- **見出しのくっついた行と別名**: BS で、土台の名前の頭の「資本剰余金」「利益剰余金」「その他利益剰余金」を `strip_heads` と同じ規則で落として引く（「利益剰余金その他利益剰余金繰越利益剰余金」→「繰越利益剰余金」）。「その他の包括利益累計額（合計）」は「評価・換算差額等」（`ValuationAndTranslationAdjustments`。XBRL の fact のラベルは要素のラベルなので連結の表示名で引けない）で引く。引いた `any` が区分の合計の要素ならそれを当てる。この後、新しく付いた区分の合計で区分を決め直し、まだ付いていない行を上と同じ当て方でもう一度引く。`strip_heads` / `PDF_HEADS` は変えない（上の 4 段の土台を変えないため）。
- **CF の期末残高**: 「現金及び現金同等物の（当・四半期・中間）期末残高」「期末現金及び現金同等物残高」（`CLOSING_CASH_RE`）は、会社の XBRL のラベル「現金及び現金同等物の残高」「現金及び現金同等物」（名前の対応）の要素、無ければ辞書の同じ名前の要素を当てる。XBRL の CF の残高は当期の fact しか読まないので、要素は期末の 1 つ（ラベルは期末と書かない）。上の 4 段で付かないのは、CF の PDF と XBRL が同じ期末に揃う組（値の対応）が無く、名前も合わない会社。期首残高（`OPENING_CASH_RE`）は今までどおり当てない。
- **「その他」**: 置き場（上と同じ決め方。PDF では付いた `Subtotal…` の行か土台が「小計」の行が小計）が決まり、同じ置き場に「その他」が 1 行だけの行に、会社の XBRL で同じ置き場の「その他」が 1 つの要素に決まればそれ（`Names.others`）、無ければ辞書の `others` を当てる。PDF の区分に小計の行が無く、会社か辞書にその区分の `:小計まで` があるときは当てない（小計の前か後ろか分からないため）。区分が決まらない・区分の外の行には当てない。
- **累計額**: 土台が上の 3 つの名前の行に、間の累計額の行を飛ばした 1 つ上の行が要素に付いていて、すぐ下の行が「…（純額）」の時だけ、会社の XBRL の同じ並び（`Names.accumulated`。`ext:` も使う）、無ければ辞書の `accumulated` を当てる。1 つ上の行が付いていなければ当てない。「その他」などこの段で付いた行の後に引く。

**作り直す時**: 四半期に 1 回、全銘柄で作り直し、`xbrl_pdf_matches` を全銘柄で付け直す。

#### 同じ期末に複数の出どころがある場合の統合と行の並び

表ごとに `有報` ＞ `短信 XBRL` ＞ `短信 PDF`（同一出どころは提出が新しい方）を主とし、短信 PDF は最新開示と同一基準で最大行数の 7 割以上ある最新開示を土台にして、後続の一部訂正 PDF で土台行の値置換（「その他」・`#n` 付きを除く）と要素対応済み追加行の補完を行う。主に無い要素は下位の出どころ（PDF は対応済み行のみ）で補う。ただし BS は、主が表示リンクの BS の区分を持つ XBRL なら、その区分に載っていない要素を補わない（主はその科目を別の行に含めて印字している。`2962` の有報の 受取手形及び売掛金 に、短信 XBRL の 受取手形・売掛金 が足されて資産の行の和が資産合計を超えていた。KAB-547）。PDF が主の列で要素の付かなかった BS の行は、同じ土台の名前が区分を変えて 2 つ以上あれば、PDF の `#n` でなく（区分, 区分の中の何番目）で全期そろえた名前にする（`_key_by_section`。区分は付いた区分の合計の行から決め、`MARKER_ORDER` の区分の順に土台、`#2`、`#3` …。`1771` などで流動資産の貸倒引当金が無くなった期に、投資その他の資産の貸倒引当金が `#n` の振り直しで流動資産の行の名前になっていた。KAB-546）。行順は最新期を土台に古い期の行を差し込み、分断された同一見出し塊は最多行数の塊へ集約する（`gather_heads`）。表示名が同じ（または「および→及び」「または→又は」・NFKC 正規化で一致する表記ゆれ、例: `68570` BS の「負債および資本」と「負債及び資本」）で要素や表記が違う見出しは、1つの書類に両方あるときは別の見出しのままにする（PLの「（内訳）」。当期純利益の帰属と包括利益の帰属）。どの書類にも両方無いときは最新期の要素・表示名に集約する（KAB-494・KAB-603。77410 の費用、87050 の営業収益、77440 の利益に対する調整項目、43160 と 87690 のその他の包括利益累計額）。各行には画面の強調表示用に集計上の役割 `role`（内訳 `item`・小計および中区分合計 `subtotal`・大区分合計および主要段階利益 `total`・総計 `grand`）を付与する。

#### 会計基準で表を分ける

列を古い順に見て IFRS かどうかが連続する塊ごとに別の表に分ける（`_standard_runs`、区分があれば `xbrl_presentation_roles.is_ifrs`、無ければ `accounting_standard`、ただし前後が同じ基準で 1 列だけ違う PDF 列［`2664`・`6930`・`9613`・`9719`］は前後に合わせる。日本基準と米国基準は分けない）。`periods` は全表を通して新しい方から数え、最新基準の表を通常位置に、前の基準の表を `earlier`（新しい順）に入れる。

#### 区分と見出し

行の区分と見出しは表示リンクから決める。

- 深さがある書類（`_tree_heads`）は `Abstract` 祖先の表示名をそのまま見出しに出し、金額要素配下の子は `indent` で字下げする。例外として、(1) 見出しの合計要素より後ろの子付き見出しは出さず（`6094`・`4052`。ただし大区分・区分の見出し［`HEAD_GROUPS`・`HEAD_SECTIONS`］は出さないのではなく、合計の済んだ親の見出しの外に出して親と並べる。`6981` は `資本の部` を `負債の部` の中の負債合計の後ろに、`3372`・`3050` 個別は `評価・換算差額等` を `株主資本` の中の株主資本合計の後ろに、`2930` 個別は `固定負債` を `流動負債` の中の流動負債合計の後ろに置き、資本・評価換算差額等・固定負債の行が親の見出しの下に出ていた。KAB-549）、(2) 子なし見出しは後続の兄弟要素を合計まで配下にし、(3) `Subtotal*` の子は字下げせず、(4) 新株予約権・非支配株主持分や CF の換算差額・増減額・期首期末残高・`*CCE`（`OUTSIDE_SECTIONS`）は株主資本や営業・投資・財務の見出し外へ出す。(5) 金額のある区分の合計の要素（`投資その他の資産` など、`<要素>Abstract` が見出しの要素）の子として字下げした行は、表のほかの行が同じ道筋の下にその見出し（`<要素>Abstract`）を宣言していれば、その見出しの下に 1 段浅く置く（`Part.under`。`8306` 個別の四半期の BS は 投資その他の資産 の金額の下に内訳を並べ、有報は見出しの下に並べる。有報に無い「その他」だけが合計の下に字下げされていた。KAB-546）。
- 行統合判定用の「大区分・区分」（`_flat_parts`、KAB-481）は深さを見ず並びから決める（BS 大区分は `AssetsAbstract`・`LiabilitiesAbstract`・`NetAssetsAbstract` と IFRS 版、BS 区分は `CurrentAssetsAbstract`・`NoncurrentAssetsAbstract`・`PropertyPlantAndEquipmentAbstract`・`IntangibleAssetsAbstract`・`InvestmentsAndOtherAssetsAbstract`［`InvestmentsAndOtherAssetsGross` を含む］・`DeferredAssetsAbstract`・`CurrentLiabilitiesAbstract`・`NoncurrentLiabilitiesAbstract`・`ReservesUnderTheSpecialLawsAbstract1`・`ShareholdersEquityAbstract`・`ValuationAndTranslationAdjustmentsAbstract` と IFRS 版 `CurrentAssetsIFRSAbstract`・`NonCurrentAssetsIFRSAbstract`・`CurrentLiabilitiesIFRSAbstract`→`TotalCurrentLiabilitiesIFRS`・`NonCurrentLiabilitiesIFRSAbstract`→`NonCurrentLabilitiesIFRS`・`EquityAttributableToOwnersOfParentIFRSAbstract`、CF は営業・投資・財務の 3 区分、日本基準 PL は売上原価・販管費・営業外収益費用・特別利益損失の 6 区分。流動・固定資産負債と営業・投資 CF は `OPEN_AFTER_TOTAL` で後ろに見出しがある間は合計で閉じず、見出しなし 1 行合計は `LONE_SECTIONS` でその区分にする）。各行は値がある最新書類の区分・見出しを使い、深さの無い古い書類のみの行は同じ区分の合計行から宣言見出しを借りる。
- 書類の宣言がその書類自身の値と合わない要素は `config/xbrl/row_place_fixes.json`（パスは `app/config.py` の `XBRL_ROW_PLACE_FIXES_PATH`）に（会社・連結か・表・書類・要素）で載せ、材料を読むとき（`load_both`）に直す。規則で自動に直さない。`as` が無い行は、その書類のその要素の宣言を捨て、値のあるほかの書類の宣言に従う（`8706` は 2026-09 の半期報告書と短信 XBRL の 2 通だけが貸倒引当金を投資その他の資産の外に宣言、`7157` 個別は半期報告書だけが繰延税金資産をその他資産の子に宣言。KAB-591）。宣言を捨てた要素にほかの書類の宣言が無ければ、宣言の無い行として上の行の置き場所を引き継ぐ（`8609` の 2019-03〜2022-03 の有報 8 通は有形固定資産の内訳を全部固定資産の直下に並べた。ほかの内訳の行は 2023-03 以降の宣言で有形固定資産の子になるが、建設仮勘定は 2023-03 以降に値が無いので、8 通の宣言を捨てて上の リース資産 の置き場所にする）。`as` がある行は、その書類のその要素の値を `as` の行に出し、置き場所はその書類の宣言のまま（無形固定資産を合計 1 行で出した四半期に、投資その他の資産の その他 を `OtherIA` で付けた `1866`・`3045` 個別・`8945` は `OtherIOA`。ほかの要素の行に入れられない科目は `<要素>#<印>` の別の行。印はふつう書類: `4395` の投資その他の資産の中の のれん、`6037` 個別の 2017-01 の固定負債の本社移転損失引当金［同じ要素を後の期は流動負債に宣言］。同じ置き場所の書類が何通もあれば 1 つの印にまとめる: `9306` の 2017-03〜2022-03 の有報 6 通が 引当金 の見出しの中に宣言した役員退職慰労引当金は `#ProvisionNCL`［2023-03 以降の有報は固定負債の直下に宣言し、引当金の値を持たない］）。別の行が元の要素の行と大区分・区分と行名が同じなら、`row_merge_exclusions.json` にも組を載せる（載せないと「タグが替わって割れた行をまとめる」で元の行にまとまる）。

- PDF が主の列で、短信 PDF の行に当たる要素が値学習・名前の当て方と違う行は `config/xbrl/pdf_row_fixes.json`（パスは `app/config.py` の `XBRL_PDF_ROW_FIXES_PATH`）の `rows` に（会社・連結か・表・書類・PDF の行名 → 要素）で載せ、材料を読むとき（`load_both`）に書類へ持たせて、当てた結果を上書きする（財務3表と `xbrl_pdf_matches` の両方。`matched_by` は `fix`）。同じ書類のほかの行に付いた同じ要素は外す。規則で自動に直さない。画面の行名が学んだ PDF の行名でほかの要素と重なるものは、同じファイルの `labels` に（会社・要素 → 行名）で載せる。`6071` の 26.03 1Q の短信 PDF は使用権資産を総額・減価償却累計額・純額の 3 行で載せたが、25.12 通期の短信 PDF が純額 1 行を「使用権資産」と載せていたため「使用権資産 → `RightOfUseAssetsNet`」と学び、総額が純額の行に入って累計額と純額が PDF だけの行に割れていた。行名も通期の PDF の「使用権資産」になり、総額の `RightOfUseAssets` と同じ名前の行が 2 つ並んでいた。XBRL の列は要素を持つので、この当て方は通らない

#### タグが替わって割れた行をまとめる

DB は要素名のまま残し、表の組み立て時（`periods` で切る前）に、(1) 大区分・区分が同じ、(2) 表示行名が同じ（「その他」、「減価償却累計額」「減損損失累計額」で始まるもの、「貸倒引当金」を除く）、(3) 値のある期が全列で 1 つも重ならない（3 行以上の塊は全組非重複のときのみ）、(4) 単位種別（金額・1 株当たり）が同じ、(5) 両方とも XBRL 要素（標準または `ext:`、`pdf:` は対象外）であり `config/xbrl/row_merge_exclusions.json` の除外ペアでない行どうしを最新期の行へ統合し、統合元キーを `merged_keys` に残す。除外ペアは、行名と大区分・区分が同じでも見出し（区分の中の段）が替わった別の科目（`7601` の 自社有形固定資産 の中の機械装置・リース資産と有形固定資産の直下の標準の要素、`9006`・`9007` 個別の 付帯事業・その他事業 と 不動産・レジャー事業・生活サービス事業 の固定資産。KAB-591）。まとめると旧の期の値が新しい見出しの下に出て、旧の見出しの中の減価償却累計額の親が無くなる。

#### 財務3表の限界

未対応の CF 期首残高行は表にも `xbrl_pdf_matches` にも出さない（`xbrl_facts` には残る）。四半期新設科目・`0` の行・米国基準 PDF・推定振り分けに合わない独自並び・会社独自の区分合計要素（`ext:`）などは自動対応の限界となる。

#### 当てはめの規則を変えるときの答え合わせ（`tools/xbrl/pdf_match_check.py`）

`three_statements.py` の当てはめ（第 1〜5 段）か標準ラベルの辞書を変える PR は、本番の全銘柄で `tools/xbrl/pdf_match_check.py check` を流して前後を比べる。合成のテストは通っても、実データで付いていた行が外れることがある。DB は読み取り専用で開く。

- 測り方: 同じ期末に PDF と XBRL の両方がある期を、新しい方から `--periods`（既定 3。0 で全部）期、1 期ずつ伏せる。伏せた期を PDF だけの期として `pdf_only_matches` で対応を付け、伏せた XBRL との値の対応（`learn_pair`。辞書は使わない）を答えにする。伏せた期以外の XBRL は全部使う（`--before-only` で伏せた期より後の XBRL も使わない）。
- 数え方（最後に `stat` の行で出す）: 付いた行は `new_matched_<matched_by>_<ok|wrong|no_truth>`（答えと同じ要素 / 別の要素 / 答えが無い）、付かない行は `new_unmatched_<std_known|std_new|ext_known|ext_new|not_in_xbrl>`（答えが標準の要素か会社独自の要素か、その要素がほかの期の XBRL に出ているか。`not_in_xbrl` は伏せた XBRL に同じ値が無い）。
- 前後の比較: `--old <古い版の three_statements.py>`（`git show origin/main:app/xbrl/three_statements.py > …` で取り出す）を渡すと、同じ行を古い版でも付けて `change_<fixed|broke|gained_ok|gained_wrong|gained_no_truth|lost_ok|lost_wrong|lost_no_truth|changed_other>` を数え、`--changes <TSV>` に 1 行ずつ書く。新しい版は既定でこのチェックアウトの版（`--new <ファイル>` で別の版）。`broke` と `lost_ok` が、正しく付いていた行が悪くなった数。
- 標準ラベルの辞書: 既定は版ごとの既定の辞書（`config/xbrl/standard_labels.json`）。`--labels <JSON>` で両方の版に同じ辞書を渡す。`--exclude-own` は全銘柄の XBRL を先に読み（`label_dictionary.collect`）、判定する銘柄を除いて作った辞書で測る（自分の XBRL で自分を当てない。辞書の載せる条件や作り方を変えたときはこれで測る）。
- `--remain <TSV>`: 付かなかった行を、行名・土台・区分・前後の行と付いた要素・答えの要素・伏せた XBRL に同じ名前の行があるか（要素と値）・同じ値がどこかにあるか、つきで書く。
- `--wrong <TSV>`: 答えと別の要素に付いた行を、付いた要素とそのラベル・その要素が伏せた XBRL にあるか（あればその値）・答えの要素とラベル・答えの要素がほかの期の XBRL に出ているか、つきで書く（期ごとに要素名を変える会社の、名前が変わっただけの行と、科目の取り違えを分けるのに使う）。
- `--code`（繰り返し可）で銘柄を絞る。`--max-seconds` を超えたら次の銘柄に進まず集計を出す。全銘柄は数時間かかる。`matches.py`・`financials.py` と同時に流さない（読み取りが書き込みを待たせ、`database is locked` になる）。
- `subset --db <本番の DB> --out <新しい DB> --code …`（または `--codes-file`）: 指定した銘柄の `stocks` と `xbrl_filings` / `xbrl_contexts` / `xbrl_facts` / `xbrl_elements` / `xbrl_presentation_roles` だけの DB を作る（悪くなった行のある銘柄を手元で追う用）。できた DB はコミットしない。

### 出どころをまたいで要素名で引く

「財務3表」の対応を `xbrl_pdf_matches` に保存し、有報・短信 XBRL・短信 PDF を同じ要素名で SQL から引く集計用ビュー `xbrl_statement_values` を提供する（画面・API は参照しない）。

#### 対応テーブル `xbrl_pdf_matches` と `matches.py`

対応テーブルへの格納条件と更新コマンドは以下のとおりである。

- `stocks` 登録銘柄のうち、同一銘柄・同一期末・同一連結区分に本表付きの `'edinet'`・`'tdnet'`（`'parsed'`）が無い `'tdnet_pdf'` filing の当期 fact ごとに 1 行を持つ（`three_statements.pdf_only_matches` で `element_key`・`sign`・`matched_by` を計算し、未対応は `NULL`、未対応の CF 期首残高は入れない）。PDF 再読込時は `delete_filing_payload` が先に古い対応を消す。
- 付け直しは `tools/tdnet_pdf/matches.py`（全銘柄または `--code`、`--batch N` で進捗出力と `database is locked` の `5` 秒待機再試行、全銘柄時は `stocks` 外銘柄の既存行も `DELETE` し、最終行に `pdf_matches codes=... filings=... matched=... unmatched=... failed=... skipped=... deleted=...` を出力）で行う。cron には入れず、PDF 読み直し時（`financials.py` 末尾で自動実行）・`three_statements.py` 変更時・四半期ごとの有報出揃い後に流す。
- 全社の標準ラベルの辞書（第 5 段）を作り直したときは、全銘柄で付け直す。

#### 集計用ビュー `xbrl_statement_values`

列は `code`・`period_end`・`period_type`・`is_consolidated`・`context_type`・`period_start`・`element_key`・`value`・`unit`・`source`・`filing_id`・`submitted_at`・`fact_id`・`matched_by` の 14 列である。

- XBRL 側は `'edinet'`・`'tdnet'` の当期・連結単体次元のみ・円または円株・`jppfs_cor` または `jpigp_cor` または会社独自要素を対象とし、`Q2` は `120` 日以上、`Q3` は `210` 日以上の累計 duration だけを出す。PDF 側は `xbrl_pdf_matches` で要素が付いた行だけを `value_num * sign` で出す。
- `row_number() OVER (PARTITION BY code, period_end, is_consolidated, element_key ...)` で `edinet` ＞ `tdnet` ＞ `tdnet_pdf`・提出日時降順・`fact_id` 昇順の 1 行を選ぶ（外側の `code`・`element_key`・`period_end`・`is_consolidated` 条件は内部にプッシュダウンされ、`+e.namespace_key` で名前空間索引の誤使用を防ぐ）。
- 「財務3表」API との違いとして、注記・株主資本等変動計算書の要素も出る点、XBRL 併用期の PDF 補完セルは出ない点、および API が同一要素に複数の当期 duration があるときだけ短い期間を落とすのに対しビューは `Q2`・`Q3` の日数（`120` 日・`210` 日）で一律に累計へ絞る点（`36790` の `ProfitLossBeforeTaxIFRS` など 3 か月単独値を除外）がある。代表的な検索クエリは以下のとおりである。

```sql
-- 前受金の全社・全期間（連結）
SELECT code, period_end, period_type, value, source
FROM xbrl_statement_values
WHERE element_key = 'AdvancesReceived' AND is_consolidated = 1
ORDER BY code, period_end;

-- 1 銘柄の全要素
SELECT period_end, element_key, value, source
FROM xbrl_statement_values
WHERE code = '98800' AND is_consolidated = 1
ORDER BY element_key, period_end;

-- 要素が付かなかった PDF 行の多い順（tools/tdnet_pdf/matches.py --unmatched 50 相当）
SELECT m.statement, e.local_name, count(*) AS n
FROM xbrl_pdf_matches m
JOIN xbrl_facts f ON f.id = m.fact_id
JOIN xbrl_elements e ON e.id = f.element_id
WHERE m.element_key IS NULL
GROUP BY 1, 2
ORDER BY n DESC
LIMIT 50;
```

## 運用

### cron と CLI

`deploy/crontab` では 12〜23 時の毎時 `00` 分に `tdnet_xbrl_r2.py 2` → `xbrl_parse.py` → `xbrl_normalize.py` → `xbrl_statements.py` をセミコロン区切りで直列実行する。fetch が失敗しても未解析 ZIP を処理できるよう後続ジョブは走り、単独の `:10`・`:20` cron は置かない（手動再実行は `deploy/cron-run.sh` を使う）。

`xbrl_normalize.py --rebuild` は最初に全行を消すため完了まで修正履歴や売上総利益が空になる。対象を絞れるときは消さずに 1 件ずつやり直す `--since YYYY-MM-DD`（`--source tdnet` 等で限定可、`--rebuild` とは排他）を使う。

### 過去の書類の表示リンクを埋める

担当ジョブは [`xbrl/xbrl_presentation`](gen/xbrl.md#xbrlxbrl_presentation)（`jobs/xbrl/xbrl_presentation.py`、`app/xbrl/presentation_backfill.py`）である。`presentation_status` が `NULL` である `'edinet'`・`'tdnet'` の `'parsed'` filing を提出の新しい順に 1 件ずつ読み、ZIP 内の `*pre.xml`・`*.xsd`・`*lab.xml`（`AuditDoc/` 配下を除く）だけを展開して `read_presentation` → `store_presentation` で保存し `run_sqlite_write` でコミットする（cron には載せない）。

- 短信 XBRL は `--dest`（既定 `XBRL_DEST`、`fail_if_mount_down` でマウント確認）から読み、無ければ `'gone'` にする。EDINET は手元の `data/edinet` に無ければ `edinet_archives` の `'saved'` 記録を見て OneDrive（`offsite`）から直接読み、記録も無ければ EDINET API から取る（404・閲覧期間切れ・`edinet_documents` 欠落は `'gone'`、5xx・タイムアウト・OneDrive 失敗は印を付けず `errors` で終了コード `1`、EDINET メンテは打ち切って終了コード `0`、`EDINET_API_KEY` 無しや `--local-only` では欠落分を `skipped` にする）。
- EDINET から取った ZIP は `data/edinet` に溜まり翌朝の `jobs/ops/backup_data.py` が OneDrive へ移すため、`--limit` で 1 日の件数を区切る。EDINET・OneDrive 取得後のみ `--sleep` 秒待ち、`--archived-only` で退避済み EDINET 限定、`--offsite-chunk N` で `rclone copy --files-from --no-traverse` による一時フォルダ一括コピーができる。`--batch N` ごとと最終行に `xbrl_presentation done=... ok=... none=... gone=... failed=... errors=... skipped=... remaining=...` および `local=件/秒 edinet=件/秒 offsite=件/秒` を出す。
- `--redo-flat` は `presentation_status='ok'` だが深さまたは見出し表示名が欠けている書類（`seq=0` の `json_array_length(items_json, '$[0]') < 6`）を読み直す（その際 ZIP 欠落等で読めない書類は既存の行と `'ok'` を残すため `remaining` が `0` にならないことがある）。`'gone'`・`'failed'` を再実行するときは対象 filing の `presentation_status` を `NULL` に戻してから流す。

### 設定

`app/config.py` で読み込む環境変数と既定値は以下のとおりである（`.env.example` にコメントで記載し、必須キーは増やさない）。

```ini
XBRL_DEST={repo}/data/xbrl
XBRL_CACHE_DIR=data/xbrl_taxonomy
XBRL_TEXT_MAX_CHARS=1000
XBRL_PARSE_WORKERS=2
XBRL_DOCUMENTS_FILL_LIMIT=3000
```

### 非機能要件

非機能面の要件を下表にまとめる。

| 項目 | 要件 |
|---|---|
| 冪等性 | 同一 ZIP・同一 `sha256` を再パースしても fact 件数と正規化結果が変わらないようにする |
| トランザクション | filing 単位でコミット・ロールバックし、部分的な fact を残さない |
| ログ出力 | 既存バッチと同様に標準出力へ出し、`doc_id` と `code` を含める（JSON 形式は必須としない） |
| 依存ライブラリ | XML 解析は標準ライブラリベースの `app/xbrlp/`、YAML は `pyyaml`、PDF 変換は `pypdfium2` を使い、`Arelle` などの大型外部パーサは追加しない |
| 実行環境 | 本番は VPS の `.venv` 上で `data/xbrl` と同梱タクソノミ（`XBRL_CACHE_DIR`）を参照し、ローカルでは `--dest` で ZIP ディレクトリを指定する |

### テスト

`tests/xbrl/test_xbrl_parse.py` では実 ZIP を置かず、ネットワーク通信なしの合成 iXBRL fixture とローカル XSD キャッシュで以下を検証する。

1. 製造業（日本基準・連結短信）、銀行（売上相当が `OperatingRevenue` 系）、IFRS、単体のみ、カスタム要素が多い会社の 5 種類すべてで `skipped` 以外の fact が 0 件でないことを確認する。
2. BS が取れる fixture で `total_assets` と `total_liabilities + net_assets` の差が `±1` 以内であること、同一 ZIP を 2 回パースしても fact 件数が同じであること、正規化で `net_sales`・`operating_income` が期待値と一致すること、および未出の名前空間があっても `load-mappings` が成功することを確認する。

### 今後の拡張候補

現時点で未実装としている後続課題は以下のとおりである。

- 従来の `.xbrl` インスタンス単体文書のパーサ対応を残課題とする。
- 正規化層の実績値を決算詳細 API から直接返す対応（`statements` の予想列との併用）を検討対象とする。
- 生 fact テーブル群を別 DB ファイルへ分離する構成を将来の拡張候補とする。
