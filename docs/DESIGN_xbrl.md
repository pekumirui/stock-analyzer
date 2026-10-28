# TDnet XBRL 財務データ蓄積 設計書（stock-analyzer 向け）

正本の置き場は本ファイル。全体設計は [`DESIGN.md`](../DESIGN.md)。実装時の約束は [`AGENTS.md`](../AGENTS.md)。

不明点は推測で仕様を変えない。特にマッピング定義は業務知識に依存するため、§7.2 に無い科目を独断で足さない。

---

## 0. 確定事項（2026-09-07）

実装前に確認済み。覆すときはこの節と該当箇所を両方直す。

1. 第1弾の取得・パース対象は **TDnet 短信 iXBRL のみ**。EDINET の有報・半期報は KAB-73 で `source='edinet'` として足した（取得・対象・fact の絞り込みは `docs/DESIGN_edinet.md` §4）。パース・正規化・マッピングは同じものを使う。
2. 企業キーは `stocks.code`（5桁）だけ。`stocks.edinet_code`（nullable）は KAB-72 で足した（`docs/DESIGN_edinet.md` §2）。XBRL 層のキーには使わない。
3. context から連結/単体が取れなければ **連結として扱う**（`is_consolidated=1`）。
4. 生データ・正規化層は **既存と同じ SQLite**（`DATABASE_URL` / 本番 `stock.db`）に載せる。分離は容量が問題になってから。
5. 決算速報 UI / API の正本は当面 **`statements` のまま**。XBRL 層とマージしない。精度が出てから実績列の読み出し寄せを検討する。例外として、決算表の修正履歴（KAB-57）は `?rev_source=xbrl` / `EARNINGS_PARAMS["revision_source"]` で `xbrl_financials` を読める（既定は `jquants`）。読むだけで `statements` は変えない。修正履歴が読むのは `source='tdnet'` の filing だけ。

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

- EDINET 有報・四半期報告書の取得とパース（有報・半期報は KAB-73 で足した。`docs/DESIGN_edinet.md` §4）
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

[J-Quants /v2/fins/summary] ──既存──▶ statements   ← 決算速報 UI は当面ここ
[stocks] ──既存──▶ 銘柄マスタ（5桁）。XBRL 側の FK 相当は code 文字列照合のみ
```

パースが読むのは `data/xbrl`。FUSE `/home/stock/r2-share` は残置（ジョブの dest ではない）。

3 ジョブは独立して再実行できる。

| ジョブ | 実装 | 責務 |
|---|---|---|
| (1) fetch | `jobs/xbrl/tdnet_xbrl_r2.py`（既存・変更最小） | 一覧 → ZIP → dest（既定 `data/xbrl`）。skip 正本は manifest の `xbrl_id` |
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
| ソース | J-Quants 短信サマリ | TDnet XBRL 生 fact を正規化 |
| 形 | 横持ち（売上・営利・予想など固定列） | 縦持ち（標準科目 × 値） |
| UI | 決算速報・詳細の正本 | 決算表の修正履歴だけデータ源に選べる（既定は `statements`） |
| 期間 | 累計実績 + 会社予想 | 本表の当期実績に加え、EPS・配当・会社予想（通期/中間、レンジ、予想修正） |

突合（同じ開示の `net_sales` が一致するか）は実装後の検証用であり、自動で `statements` を上書きしない。

### 3.3 コード規則

既存どおり:

- 保存・結合キーは 5桁 `^[0-9][0-9A-Z]{3}[0-9]$`
- 保存ディレクトリと manifest の `code4` は 4桁のまま
- 表示の 4桁と混同しない

---

## 4. データベース設計

### 4.1 ER

```
stocks.code  <文字列照合>  xbrl_filings.code
                     │
                     ├── xbrl_contexts
                     └── xbrl_facts ── xbrl_elements
                                │
                                └── xbrl_element_mappings ── xbrl_std_items

xbrl_filings 1---* xbrl_financials *---1 xbrl_std_items
```

FK は XBRL テーブル同士にだけ付ける。`stocks` への FK は付けない（月次と同じ。`PRAGMA foreign_keys` も有効化しない）。parse 前に `db.get(Stock, code)` で存在確認する。

### 4.2 DDL（SQLAlchemy 実装時の意図）

SQLite 前提。Postgres に切っても動く型にする。`JSONB` / `GENERATED ALWAYS` / 部分 INDEX の `WHERE` は使わない。

```sql
-- 開示（1 ZIP = 1 filing）
CREATE TABLE xbrl_filings (
    id                  INTEGER PRIMARY KEY,
    code                TEXT NOT NULL,          -- stocks.code 5桁
    source              TEXT NOT NULL,          -- 'tdnet' | 'edinet' | 'tdnet_pdf'
    doc_id              TEXT NOT NULL,          -- TDnet xbrl_id（inbs ZIP 名）
    doc_type            TEXT NOT NULL,          -- 'annual'|'quarterly'|'semiannual'|'other'
    period_type         TEXT,                   -- 'FY'|'Q1'|'Q2'|'Q3'
    fiscal_year_end     DATE,
    submitted_at        TEXT NOT NULL,          -- TDnet pubdate（タイムゾーン付き可）
    title               TEXT,
    accounting_standard TEXT,                   -- 'JPGAAP'|'IFRS'|'USGAAP'|NULL
    is_amendment        BOOLEAN NOT NULL DEFAULT 0,
    raw_path            TEXT NOT NULL,          -- data/xbrl からの相対パス（manifest.path）
    raw_sha256          TEXT,                   -- manifest.sha256
    parse_status        TEXT NOT NULL DEFAULT 'pending',
                        -- 'pending'|'parsed'|'failed'|'skipped'
    parse_error         TEXT,
    parsed_at           TEXT,
    documents_status    TEXT,                   -- 原文（§15）: NULL|'stored'|'empty'
    created_at          TEXT NOT NULL,
    UNIQUE (source, doc_id)
);
CREATE INDEX idx_xbrl_filings_code ON xbrl_filings(code, submitted_at);
CREATE INDEX idx_xbrl_filings_parse_status ON xbrl_filings(parse_status);

CREATE TABLE xbrl_elements (
    id              INTEGER PRIMARY KEY,
    namespace_uri   TEXT NOT NULL,
    namespace_key   TEXT NOT NULL,   -- 'jppfs_cor' / 'tse-ed-t' / 'jpigp_cor' / 'custom' 等
    local_name      TEXT NOT NULL,
    label_ja        TEXT,
    label_en        TEXT,
    item_type       TEXT,            -- 'monetary'|'shares'|'perShare'|'string'|'percent'|NULL
    is_custom       BOOLEAN NOT NULL DEFAULT 0,
    first_seen_at   TEXT NOT NULL,
    UNIQUE (namespace_uri, local_name)
);
CREATE INDEX idx_xbrl_elements_key ON xbrl_elements(namespace_key, local_name);

CREATE TABLE xbrl_contexts (
    id                  INTEGER PRIMARY KEY,
    filing_id           INTEGER NOT NULL REFERENCES xbrl_filings(id),
    context_ref         TEXT NOT NULL,
    period_type         TEXT NOT NULL,   -- 'instant'|'duration'
    start_date          DATE,
    end_date            DATE NOT NULL,
    is_consolidated     INTEGER NOT NULL, -- 1=連結 / 0=単体。判定不能は 1
    relative_period     TEXT,            -- 'current'|'prior1'|'prior2'|NULL
    dimensions_json     TEXT NOT NULL DEFAULT '{}',
    has_dimensions      BOOLEAN NOT NULL DEFAULT 0,
    UNIQUE (filing_id, context_ref)
);
CREATE INDEX idx_xbrl_contexts_filing ON xbrl_contexts(filing_id);

CREATE TABLE xbrl_facts (
    id              INTEGER PRIMARY KEY,
    filing_id       INTEGER NOT NULL REFERENCES xbrl_filings(id),
    element_id      INTEGER NOT NULL REFERENCES xbrl_elements(id),
    context_id      INTEGER NOT NULL REFERENCES xbrl_contexts(id),
    unit            TEXT,
    decimals        INTEGER,
    scale           INTEGER,
    value_num       NUMERIC,         -- scale/sign 適用後。SQLite では NUMERIC 親和性
    value_text      TEXT,            -- 1000 文字以内の非数値
    is_nil          BOOLEAN NOT NULL DEFAULT 0,
    UNIQUE (filing_id, element_id, context_id, unit)
);
CREATE INDEX idx_xbrl_facts_filing ON xbrl_facts(filing_id);
CREATE INDEX idx_xbrl_facts_element ON xbrl_facts(element_id);

CREATE TABLE xbrl_std_items (
    id              INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,   -- 'net_sales'
    statement       TEXT NOT NULL,          -- 'BS'|'PL'|'CF'|'OTHER'
    section         TEXT,
    label_ja        TEXT NOT NULL,
    display_order   INTEGER NOT NULL DEFAULT 0,
    sign            TEXT NOT NULL DEFAULT 'natural',  -- 'natural'|'invert'
    is_required     BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE xbrl_element_mappings (
    id                  INTEGER PRIMARY KEY,
    std_item_id         INTEGER NOT NULL REFERENCES xbrl_std_items(id),
    namespace_key       TEXT NOT NULL,
    local_name          TEXT NOT NULL,
    priority            INTEGER NOT NULL DEFAULT 100,
    accounting_standard TEXT,   -- NULL=全共通
    note                TEXT,
    UNIQUE (std_item_id, namespace_key, local_name, accounting_standard)
);
CREATE INDEX idx_xbrl_mappings_elem ON xbrl_element_mappings(namespace_key, local_name);

CREATE TABLE xbrl_financials (
    id                  INTEGER PRIMARY KEY,
    code                TEXT NOT NULL,
    filing_id           INTEGER NOT NULL REFERENCES xbrl_filings(id),
    std_item_id         INTEGER NOT NULL REFERENCES xbrl_std_items(id),
    is_consolidated     BOOLEAN NOT NULL,
    period_type         TEXT NOT NULL,
    period_start        DATE,
    period_end          DATE NOT NULL,
    value               NUMERIC NOT NULL,
    unit                TEXT NOT NULL DEFAULT 'JPY',
    value_kind          TEXT NOT NULL DEFAULT 'actual',  -- actual|forecast|forecast_previous
    range_side          TEXT NOT NULL DEFAULT 'point',   -- point|lower|upper
    target_period       TEXT NOT NULL DEFAULT 'FY',      -- FY|H1|Q1|Q2|Q3|year_end|annual
    source_fact_id      INTEGER REFERENCES xbrl_facts(id),
    source_element_id   INTEGER REFERENCES xbrl_elements(id),
    source_layer        TEXT,            -- 'primary'=本表 / 'summary'=短信サマリー(tse-ed-t)
    generated_at        TEXT NOT NULL,
    UNIQUE (filing_id, std_item_id, is_consolidated, period_end, value_kind, range_side, target_period)
);
CREATE INDEX idx_xbrl_financials_lookup
    ON xbrl_financials(code, std_item_id, is_consolidated, period_end, value_kind);

CREATE TABLE xbrl_job_runs (
    id              INTEGER PRIMARY KEY,
    job_name        TEXT NOT NULL,   -- 'parse'|'normalize'|'load-mappings'
    status          TEXT NOT NULL,   -- 'running'|'success'|'failed'
    processed_count INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    message         TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT
);
```

`unmapped` は VIEW ではなく `jobs/xbrl/xbrl_parse.py report-unmapped` のクエリで出す（SQLite の VIEW 運用を増やさない）。

### 4.3 設計意図

- **縦持ち**: TDnet タクソノミも年度改訂される。列追加では持たない。
- **正規化層は再生成可能**: マッピング修正 → `--rebuild` で `xbrl_financials` を作り直す。手で INSERT しない。
- **マッピングは element_id ではなく `namespace_key + local_name`**: 年度で `namespace_uri` が変わる。element 行が増えても YAML を書き換えなくてよい。
- **金額は float にしない**: 既存の `statements` は Float だが、生 fact は `NUMERIC`（Decimal）。正規化層も同様。
- **容量**: 本番 `stock.db` は既に約 1.1GB。短信 iXBRL は有報より小さいが、全 fact を数年分入れると億行にはならない想定でも数千万行になり得る。第1弾は同一 DB。膨らんだら `xbrl_*.db` への分離を検討（今は作らない）。
- **source**: `tdnet`（短信 ZIP。`doc_id` は ZIP 名）、`edinet`（有報・半期報）、`tdnet_pdf`（ZIP の無い短信 PDF。`doc_id` は開示番号 14 桁。`raw_path` は `tdnet_pdf:<開示番号>`。KAB-173、§16.5）。同じ 14 桁の `tdnet` が `parse_status='parsed'` になったら `tdnet_pdf` の filing は消す。`failed` / `no-ixbrl` の ZIP は消さない。

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

- 入力: 日数 N（cron は 1）
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
9. 成功なら `parse_status='parsed'`。例外は filing 単位でロールバックし `failed` + 要約トレース。ジョブは止めない。
10. 再パースは当該 `filing_id` の contexts を DELETE（facts も CASCADE 相当をアプリ側で消す。SQLite で FK が効かないため明示 DELETE）。

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
2b. **実績売上のサマリー補完（KAB-58 / KAB-59）**: 上の本表経路で `net_sales` 実績が 1 件も取れなかったときだけ、同じ書類の短信サマリー（`SUMMARY_NAMESPACES` = `tse-ed-t` と `pdf-sum`）の当期実績（`ResultMember`、点）を使う。連結を先に、同じ連結区分なら mapping の `priority`。本表で取れている値は上書きしない。会社独自の拡張タグはマッピングしない。出どころは `source_layer`（`primary` / `summary`）
3. **EPS・BPS・自己資本比率・ROE の実績**（`SUMMARY_ACTUAL_CODES`）: 本表コンテキストに加え `ResultMember`（短信表紙）を許す。レガシー 23 科目の実績は表紙の `ResultMember` を受けない。前期の実績 context は `is_normalizable_context` が落とす（PDF の fact を入れるときも同じ）
4. **配当**: `FirstQuarter` / `SecondQuarter` / `ThirdQuarter` / `YearEnd` / `Annual` メンバーで `target_period` を分ける。`ResultMember` は実績、`ForecastMember` は予想
4b. **有報の科目（KAB-78）**: 上の 2〜4 に当たらない科目（§7.2 の有報の科目）は、当期（`relative_period='current'`）の実績の点で、連結/単体以外の次元が無い fact。連結と単体は別の行（`is_consolidated`）。セグメント別・所有者別（`Row1Member` 等）・前期・提出日現在は入らない。mapping の `part: months` は同じ科目・context の値に 月÷12 を足す（小数 2 桁）。DEI `WhetherConsolidatedFinancialStatementsArePreparedDEI` が false の書類（連結子会社が無い会社）は、連結の軸が無い context（設備の状況・研究開発活動・自己株式等など）も単体にする（全科目。TDnet は DEI が無いので今までどおり）
5. **業績予想**: `ForecastMember`、または `LowerMember`/`UpperMember`/`MinimumMember`/`MaximumMember`（ForecastMember 無しのレンジ）。`relative_period='current'` は要求しない（通期予想の期末は短信の四半期末とずれる）。`PreviousMember` は `value_kind=forecast_previous`（今回予想は `forecast`）。`LowerMember`/`UpperMember` は `range_side`。context 名の `accumulatedq1`/`accumulatedq3` と日数（80–100→Q1、240–300→Q3）で `target_period` を付ける
6. `is_consolidated` が取れなければ 1（連結）として正規化する。NULL のまま残さない
7. mappings を `namespace_key + local_name` で結合。`accounting_standard` が NULL でない行は filing の基準と一致するときだけ。例外: 要素の `local_name` が `*IFRS` なら、filing が JPGAAP / NULL でも IFRS マッピングを使う（40530 の予想）。同一キーでは `priority` 昇順で最初の値。**IFRS の書類では `jppfs_*` 要素を正規化に使わない**（KAB-52 案B。IFRS 側に対応が無い科目は空欄）
8. `sign='invert'` なら符号反転。`xbrl_financials.unit` は fact の単位。fact が `pure`（または空）のときだけ科目の `unit`（人数・年数・比率を `persons` / `years` / `ratio` にする。KAB-147）。どちらも無ければ `JPY`
9. filing 単位で既存の `xbrl_financials` を消してから入れ直す
10. `is_required=1` が欠けたら警告ログ（`code, filing_id, std_item.code`）。処理は継続。required は本表実績だけ

通常実行は `xbrl_financials` が無い filing と、`parsed_at` が既存の `generated_at` より新しい filing だけ。`--rebuild` は `xbrl_financials` と `xbrl_segments` を全削除して全 parsed filing を再処理する。ログの `deleted` / `segments_deleted` は消す前、`financials` / `segments` は作り直した後の行数（KAB-83。正規化が変わっていなければ前後が同じ）。`--code` で銘柄を限定できる。

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
```

dest 既定は `tdnet_xbrl_r2.DEFAULT_DEST`。`--dest` で上書き。

cron は 12–23 時 JST に毎時 `fetch :00` → `parse :10` → `normalize :20`。短信 PDF の数値（§16.5）は cron に無い。normalize は未反映と再パース分だけ（マッピング変更は `--rebuild`）。

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
9. cron は 12–23 時 JST の毎時 fetch / parse+10分 / normalize+20分

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

- （済）EDINET API v2 で有報・半期報 ZIP を取り、`source='edinet'` の filing を足す（KAB-73。`docs/DESIGN_edinet.md` §4）
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

- EDINET の `keep_fact` は、当期でセグメント軸だけ（連結/単体の軸は付いてよい）の fact も残す（`app/edinet/xbrl.py`）。前期のセグメントは残さない
- `xbrl_contexts.member_labels_json`: 連結/単体以外の次元メンバー → その書類の日本語ラベル（`{"prefix:Member": "自動車"}`）。無ければ NULL。パーサは fact の要素に加えて、context の次元メンバーのラベルも引く（`app/xbrl/parser.py parse_package`）
- 過去分は `edinet_xbrl.py --reparse` でダウンロードし直さないと入らない
- デプロイ: `jobs/migrate.py`（表と列）→ `python jobs/xbrl/xbrl_parse.py load-mappings`（SEG の科目とマッピング）。そのあとの取込から入る。取込済みの書類は `edinet_xbrl.py --reparse`

### 14.3 正規化層 `xbrl_segments`

```sql
CREATE TABLE xbrl_segments (
    id                INTEGER PRIMARY KEY,
    code              TEXT NOT NULL,
    filing_id         INTEGER NOT NULL REFERENCES xbrl_filings(id),
    std_item_id       INTEGER NOT NULL REFERENCES xbrl_std_items(id),  -- statement='SEG'
    axis              TEXT NOT NULL,     -- 'jpcrp_cor:OperatingSegmentsAxis'
    member            TEXT NOT NULL,     -- 'prefix:LocalName'
    member_label_ja   TEXT,              -- その書類のラベル
    member_kind       TEXT NOT NULL,     -- segment|subtotal|other|total|reconciling|unknown
    is_consolidated   BOOLEAN NOT NULL,
    period_type       TEXT NOT NULL,     -- filing の period_type
    period_start      DATE,
    period_end        DATE NOT NULL,
    value             NUMERIC NOT NULL,
    unit              TEXT NOT NULL DEFAULT 'JPY',
    source_fact_id    INTEGER REFERENCES xbrl_facts(id),
    source_element_id INTEGER REFERENCES xbrl_elements(id),  -- どの物差しを採ったか
    generated_at      TEXT NOT NULL,
    UNIQUE (filing_id, std_item_id, axis, member, is_consolidated, period_end)
);
```

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

銘柄タブ（`docs/DESIGN_stock.md`）で短信を PDF と同じ並びで読むため、ZIP の HTML を `xbrl_documents` に入れる。数値の層とは別。パース・正規化の結果は変えない。

### 15.1 実データで分かったこと（2026-08-10〜10-23 の 304 件。種類ごとに最大 40 件）

- 短信 ZIP には `Summary/*-ixbrl.htm`（サマリー）と `Attachment/qualitative.htm`（タグの無い HTML。添付資料まるごと: 定性的情報・財務諸表・注記）がある。`qualitative.htm` は本決算・中間・四半期の短信でほぼ全件、REIT は 39 件中 28 件。訂正は訂正箇所だけの書類で欠けることがある
- 5933（2027/3 中間期）でサマリー＋`qualitative.htm` を Chromium で PDF にすると 12 ページ、数字 650 個と文字が元の PDF と一致した。違いはページのヘッダー・フッター、ロゴ、フォント。外の画像は TDnet の FASF マーク（`release.tdnet.info/img/mark1.gif`）だけ。`script` は 0 件
- 大きさ（サマリー＋添付、1 件あたり）: HTML 平均 765KB / 中央値 584KB、gzip 後 平均 35KB。8/10〜10/23 で 2,347 件なので年 1 万件前後、DB は年 約 400MB 増える
- 数値タグの無い表（EBITDA、定性的情報の中のセグメント表、収益の分解など）は原文にしか無い
- 業績予想・配当予想の修正（`tse-rvfc…`）の ZIP はフォルダが無く、`*-ixbrl.htm` が ZIP の直下にある（`Summary/` も `qualitative.htm` も `Attachment/` も無い）。2026-10-25 時点で原文の無い TDnet の filing が 362 件あり、この形の ZIP は 0 件のまま毎時読み直されていた

### 15.2 表 `xbrl_documents`

| 列 | 中身 |
|---|---|
| `filing_id` | `xbrl_filings.id`（主キー 1） |
| `seq` | 表示順（主キー 2）。0 から |
| `part` | `summary` / `qualitative` / `attachment` |
| `file_name` | ZIP の中のファイル名（パスなし） |
| `html_gz` | 元の HTML（BOM 込みのバイト列）を gzip |
| `bytes` / `sha256` | 圧縮前 |
| `created_at` | 入れた時刻（UTC） |

`xbrl_filings.documents_status`: 原文を作ったかどうか。NULL（まだ）/ `stored` / `empty`（選べる HTML が 0 件）。

選び方（`app/xbrl/documents.py pick_documents`）: `Summary/` の `*-ixbrl.htm`（名前順）、続けて `qualitative.htm` を 1 つ。`qualitative.htm` が無いときは `Attachment/` の `*-ixbrl.htm` をファイル名順（`0101010-…` の並びが本表の順）。`Summary/` も `qualitative.htm` も `Attachment/` も無いフォルダなしの ZIP（`tse-rvfc` など）は、ZIP の中の `*-ixbrl.htm` を名前順に `summary` として入れる。それでも無ければ 0 行で、`documents_status='empty'` にする。

既存の表に入れない: `xbrl_filings` は取込・正規化・API で何度も引く表で、1 行 35KB の BLOB を載せない。`xbrl_facts.value_text` は fact 単位で 1000 文字で切る（§1.2）。`qualitative.htm` はタグが無く fact にならない。

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

| 列 | 中身 |
|---|---|
| `disclosure_no` | TDnet の docID（主キー） |
| `code` / `disclosed_date` / `disclosed_time` | `statements` から |
| `title` | 1 ページ目の題名。取れなければ `statements.type_of_document` |
| `status` | `ok` / `missing`（PDF が 404）/ `failed`（取得・変換の失敗。`error` に理由） |
| `html_gz` / `bytes` / `sha256` | 変換後の HTML（gzip。大きさとハッシュは圧縮前） |
| `pages` / `pdf_bytes` / `pdf_sha256` | 元の PDF |
| `converter` / `created_at` | 変換器の版と入れた時刻（UTC） |

### 16.4 取込 `tools/tdnet_pdf/ingest.py`

- 対象: 指定した銘柄（`--code` 複数可、`--all` で全銘柄）の `statements` のうち、XBRL の原文（`xbrl_documents`。ZIP 名の後ろ 14 桁で照合）も、この表の `ok` / `missing` も無い回。新しい順。`failed` は `--retry-failed` のときだけ
- 1 件ずつ 取得 → 変換 → 保存（commit）。間隔は `--sleep`（既定 1 秒）、件数は `--limit`。IR BANK が 429 / 503 を返したら上限とみなして止める（終了コード 0）。途中で止めても次の実行で続きから
- 最後の 1 行: `tdnet_pdf stopped=<done|limit|rate_limited> ok=… missing=… failed=… remaining=…`。`remaining` はこの実行のあとに `targets()` を取り直した件数で、`failed`（この実行で failed にした件数）とは別。`--retry-failed` 無しでは failed 済みは残件に入らない。`stopped=done` なのに remaining>0 なら、実行開始時の対象に含まれなかった回（実行中に statements が増えた等）が残っている。`--dry-run` は対象の件数と先頭 10 件だけ
- 取った PDF が `/Linearized` の `/L`（ファイル全体の長さ）より短ければ、途中で切れたものとして `failed`（`truncated pdf`）。IR BANK に途中までしか無い PDF がある（79520 の 20260212556420 は 277,188 バイト中 76,224 バイト。pdfium も pdfminer も poppler も読めない）。旧変換器（pdfplumber）でも読めないのでフォールバックはせず `failed` のまま残し、`--retry-failed` で取り直す
- VPS で動かす（bot が銘柄を指定して毎日）。pypdfium2 は `requirements.txt` に入れた（KAB-169 で pdfplumber から替えた）
- 規模の見込み: `statements` 約 9.6 万件を全部入れると DB は約 4GB 増える（1 件 43KB）

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

読める DocType は `{1Q|2Q|3Q|FY}FinancialStatements_{Consolidated|NonConsolidated}_{JP|IFRS}` と `EarnForecastRevision` と `DividendForecastRevision`。REIT、米国基準、`4Q` / `5Q`、日付の欠けは filing を作らない。1Q / 3Q に CF が無くても失敗にしない。PDF 1 ページ目の題名（`tdnet_pdf_documents.title`）に「速報値」がある開示は filing を作らない（`absent` / `flash-results`。failed に数えない）。訂正のお知らせは `is_amendment=True`。

HTML は svg1（`<line>`）でも svg2（`<path>`）でも、`<text>` の位置だけを見る。見出しと列の役割は `app/xbrl/pdf/_tanshin.py` の表。百万円は円（10^6 倍）、百分率は `pure`（39.1 → 0.391）。設備投資の △ は負の fact のまま入れ、`std_items` の `sign: invert` で `capex` の行が正になる。「設備投資額」はマッピングしない。IFRS の経常利益はマッピングしない（`standard: JPGAAP` だけ）。IFRS 本表の売上原価・販管費は ZIP に合わせて正にする（印字の △ を外す）。

context は ZIP の短信サマリーと同じメンバー（`ResultMember` など）と、`CurrentYearDuration` / `CurrentAccumulatedQ2Duration` / `NextYearDuration` のようなトークンを使う。前期の実績は `is_normalizable_context` で fact ごと落とす。印字の行は、マッピングが無くても残った列については fact として残す。

読み方（ZIP・J-Quants と突き合わせて直したもの。`READER_VERSION=5`）:

- ページ番号（`- 6 -`）と柱（ページの上下の帯で 3 ページ以上同じ高さに出る行。2 行に分かれる柱も行ごと）は読む前に落とす。ダッシュが金額の「－」と読まれて幻の列になり、列見出しを吸っていた。ページ番号の判定は上下の帯の外だけ（表の途中の「- 104」を落とさない。KAB-220）
- 列見出しは、最初の数値の行のすぐ上に続く行だけ。上にたどって「。」を含む行・(注) / ※ / ＝ で始まる行・空白を除いて 40 字を超える行で切る（KAB-216。9983 の「＝売上収益－売上原価－…」も切る）。数値の列の幅（左端は数値の左端の最小）に重なる語は、重なりが正の列すべてに載せる（またがる純利益の見出し）。端だけ接する隣の列には載せない（KAB-213 の経常利益）。重ならない語は中心がいちばん近い列（80pt 以内）。右端だけで当てると中央寄せの見出しが付かず「右端が当期」の決め打ちになる（伊藤忠は当期が左、増減額が右）。表の右上の注記（「％表示は、対前期増減率」）と「(注)」の行は見出しにしない
- 金額は、直前が行頭・空白・△▲・～ のときだけ取る（語の途中の数字や電話番号の末尾は金額にしない。KAB-216）。`※` / `※n` は後ろに空白があるときだけ消してから金額を取る（KAB-214）。配当の「40 円」の円は金額の飾り（KAB-213）
- 「（訂正前）」から「（訂正後）」の手前までを捨て、訂正後の表を使う（KAB-212）
- 数値より上にずれた行名（数値のすぐ上で、列より左からだけ始まる行）は見出しにせず次の数値の行の行名にする。値だけの行のあと、「(予想)」「額（△は減少）」は前の行の続き。前回/今回/前期で始まる行は新しい行名。業績予想・配当予想の修正は Forecast の行だけ残す（前期実績を今期末の実績にしない）。pdf-sum の行名の先頭の「連結」「個別」は対応表に渡す前に落とす（KAB-213）
- 修正開示の会計基準は、同じ銘柄の `statements`（`*_IFRS` / `*_JP`）のうち日付が近いものから取る。基準は指紋に含める
- 列の役割は日付（「至 2026年6月30日」）を先に、無ければ年月（「2027年3月期」「2026年6月末」、和暦「令和8年」「8年6月期」）。語が分かれた見出し（「2026年」「6月末」）はつないで読む。期間の長さは語（第1四半期・中間）で決める
- 本表で捨てる列: 増減・比率（%・前年同期比・売上収益比）・3 か月だけ（「第2四半期連結会計期間」）・予想（本表の後ろの「業績予想の修正」表）。同じ役割の列が 2 つなら見出しで決まった方を残し、どちらも見出しで決まっていれば両方捨てる。ダッシュだけの列は役割を付けない
- △ だけ右端が数 pt ずれる列（JT）は、同じ行に並ばない 8pt 以内の列を 1 列にまとめる
- 本表は決算短信ごとに BS・PL・CF の最初の 1 つ（数値の行があるもの）。見出しは行頭から（「自動車等セグメントと…区分した連結損益計算書」は本表でない）。連結の短信は「連結」のある見出し、非連結は無い見出し（子会社・単体の表を読まない）。見出しの後ろの補足（「[第1四半期連結累計期間(…3ヵ月間)]」）は外して判定。ほかの「…計算書」「…対照表」と、後ろの注記（「(…に関する注記)」、文章の行）でブロックを切る
- サマリーは 1〜2 ページ目だけ、ページをまたがない（目次のページ番号を予想の値にしない）。「(2)個別財政状態」「(参考)個別業績」は単体の値として読む。実績の表で期の読めない行（「(金融を除く)」）は読まない。同じ表で同じ科目・同じ期に違う値が出たら両方捨てる
- 配当は ZIP と同じく単体（`is_consolidated=0`）。context の ref に `_NonConsolidated` を付け、連結の本表の context と取り違えない。列見出しが時点（「第１四半期末」）だけの列は 1 株当たり配当金
- レンジ予想は 3 行（「下限 / ～ / 上限」「下限～ / ダッシュ / 上限」「下限 / 行名 / ～上限」）を 1 行の下限・上限にする。「5,100～ 30.9～」のように語をまたいで 1 つのレンジと読まない
- 行名は `label_key` でそろえる: 「又は」の後ろを落とす（「営業利益又は営業損失(△)」→ 営業利益）、「(△…)」を落とす、末尾の `(A)` / `(B)` を落とす、四半期・中間の利益は当期の名前、「X利益の帰属 親会社の所有者」→ 親会社の所有者に帰属する当期利益、「営業活動に関する／から得た…(純額)」→ 営業活動によるキャッシュ・フロー、「四半期末残高」→ 期末残高。折り返した行名は、前の行が「及び」等で終わるか、値だけの行の後なら前の行の続き。「損失」で始まる数値の無い行は前の行名の尾。数値の無い「営業/投資/財務活動によるキャッシュ・フロー」と BS の「資産」「流動資産」等は区分見出しとして落とす（KAB-216）
- サマリーの「第2四半期」（末尾が「末」でないもの）は累計 Q2（KAB-215）
- 本表ページが文字化けした PDF は `parsed` のまま残し、`parse_error` に `garbled pages=3-14` のように印を付ける。1〜2 ページ目のサマリー（売上高・EPS）は入れる。文字が図形だけで表が 1 つも無いときだけ `failed`（`no in-scope table`）。`tools/tdnet_pdf/financials.py` の最後の行に `garbled=` を出す（KAB-218）

突き合わせの結果（2026-10-26。`verify_financials.py`。共有データの TDnet ZIP と、同じ回の IR BANK の PDF）:

| 組 | 一致 | 不一致 | ZIP だけ | PDF だけ |
|---|---|---|---|---|
| 開発に使った 85 組（日本基準 連結・非連結、IFRS 22） | 2,817 | 0 | 63 | 17 |
| 使っていない 57 組（日本基準） | 1,886 | 0 | 122 | 41 |
| 使っていない 30 組（IFRS） | 909 | 0 | 54 | 9 |

「ZIP だけ」は減価償却費・設備投資（PDF の CF に行が無い、行名が違う）、研究開発費、決算期を変えた 37500 など。開発の組の 63 は、ツールが `expected_zip_only` に分ける 1Q・3Q の CF 14 件を含む（ZIP に CF があるので取りこぼし）。「PDF だけ」は設備投資（ZIP の要素名が対応表に無い）と 37500。J-Quants（`statements.raw_json`、ゴールデンデータの 40 社余り 177 件）とは 2,176 件一致・1 件違い（ソニーの期末の現金。PDF は CF の期末残高、J-Quants は BS の額）。REIT・米国基準は読まない。日立の「調整後営業利益」は営業利益にしない（ZIP にも無い）。

有報・半期報（EDINET）とも比べた（2026-10-26。ゴールデンデータの 43 銘柄、同じ期が両方にある 80 期）。実績 1,647 件一致・16 件違い。違いは読み取りではなく書類・定義の差: 短信の出し直し（武田。当初の短信と有報は別の数字）、短信のあと有報で数字が変わった回（26740 の BS）、売上高の定義（イオンは PDF が営業収益・EDINET の対応表が内訳の売上高、SOMPO は PDF が PL の保険収益・EDINET が注記の収益）、短信と有報で基準が違う（東京海上の減価償却費。短信は日本基準、有報は IFRS）、ROE（44930。短信の印字どおり）、ソニーの期末の現金。同じ期の短信が 2 回出たときは PDF の filing も 2 つになる（ZIP と同じ。使う側が新しい方を選ぶ）。

`tools/tdnet_pdf/financials.py` が新しい順に最大 `--limit`（既定 2000）件。cron では動かさない。新しい短信は ZIP があり `tools/tdnet_pdf/ingest.py` が PDF を取らないので、過去の PDF を入れたあとに手で流す（`--limit 100000` で全件）。後から ZIP が来た回の PDF filing は ZIP の取込が消す。指紋が同じ `parsed` / `failed` は HTML を読まない（`unchanged`）ので何度流してもよい。`READER_VERSION` を上げるか会計基準が後から決まると指紋がずれ、次の実行が HTML を読み直す。`--reparse` は指紋を無視する。マッピングだけ変えたときは `jobs/xbrl/xbrl_normalize.py --rebuild` で足りる。

ZIP の `parse_manifest_row` は、パースが `parsed` のあと、失敗を `mark_failed` する `try` の外で `sync_pdf_filing(db, doc_id の後ろ 14 桁)` を呼ぶ。PDF 側の例外は ZIP を `failed` にしない。`parse_status='parsed'` の `source='tdnet'` がある 14 桁は PDF filing を消す。

ZIP との付き合わせは `tools/tdnet_pdf/verify_financials.py` がスクラッチの SQLite で行う（本番 `stock.db` へは書かない）。提出日は `statements.disclosed_date` / `disclosed_time` を優先し、無ければ ZIP 名の先頭 8 桁から取る（開示番号からは作らない）。`statements` に J-Quants の日付がそろっているときは ZIP の期末で上書きしない（KAB-219）。テストは本番 DB も IR BANK も呼ばない。

