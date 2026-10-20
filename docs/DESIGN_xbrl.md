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
- セグメント軸付き fact の正規化層投入
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
    source              TEXT NOT NULL,          -- 第1弾は 'tdnet'
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
7. elements UPSERT（`namespace_uri + local_name`）。`namespace_key` は prefix。標準タクソノミ（jppfs / jpcrp / jpdei / jpigp / tse-ed / tse-t-ed など **prefix の先頭一致**）以外は `is_custom=1`。リストを勝手に増やさない。
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

1. セグメント等、連結/実績予想/レンジ/配当以外の次元が付いた fact は捨てる
2. **既存23項目（BS/PL/CF）の実績**: 従来どおり `has_dimensions` が空か連結/非連結だけ、かつ `relative_period='current'`。選定キーは `std_item` ごと 1 値（値を変えない）
2b. **実績売上のサマリー補完（KAB-58 / KAB-59）**: 上の本表経路で `net_sales` 実績が 1 件も取れなかったときだけ、同じ書類の短信サマリー（`tse-ed-t`）の当期実績（`ResultMember`、点）を使う。連結を先に、同じ連結区分なら mapping の `priority`。本表で取れている値は上書きしない。会社独自の拡張タグはマッピングしない。出どころは `source_layer`（`primary` / `summary`）
3. **EPS 実績**: 本表コンテキストに加え `ResultMember`（短信表紙）を許す
4. **配当**: `FirstQuarter` / `SecondQuarter` / `ThirdQuarter` / `YearEnd` / `Annual` メンバーで `target_period` を分ける。`ResultMember` は実績、`ForecastMember` は予想
5. **業績予想**: `ForecastMember`、または `LowerMember`/`UpperMember`/`MinimumMember`/`MaximumMember`（ForecastMember 無しのレンジ）。`relative_period='current'` は要求しない（通期予想の期末は短信の四半期末とずれる）。`PreviousMember` は `value_kind=forecast_previous`（今回予想は `forecast`）。`LowerMember`/`UpperMember` は `range_side`。context 名の `accumulatedq1`/`accumulatedq3` と日数（80–100→Q1、240–300→Q3）で `target_period` を付ける
6. `is_consolidated` が取れなければ 1（連結）として正規化する。NULL のまま残さない
7. mappings を `namespace_key + local_name` で結合。`accounting_standard` が NULL でない行は filing の基準と一致するときだけ。例外: 要素の `local_name` が `*IFRS` なら、filing が JPGAAP / NULL でも IFRS マッピングを使う（40530 の予想）。同一キーでは `priority` 昇順で最初の値。**IFRS の書類では `jppfs_*` 要素を正規化に使わない**（KAB-52 案B。IFRS 側に対応が無い科目は空欄）
8. `sign='invert'` なら符号反転
9. filing 単位で既存の `xbrl_financials` を消してから入れ直す
10. `is_required=1` が欠けたら警告ログ（`code, filing_id, std_item.code`）。処理は継続。required は本表実績だけ

通常実行は `xbrl_financials` が無い filing と、`parsed_at` が既存の `generated_at` より新しい filing だけ。`--rebuild` は `xbrl_financials` を全削除して全 parsed filing を再処理する。`--code` で銘柄を限定できる。

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

cron は 12–23 時 JST に毎時 `fetch :00` → `parse :10` → `normalize :20`。normalize は未反映と再パース分だけ（マッピング変更は `--rebuild`）。

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

第1弾の BS/PL/CF 23項目に加え、KAB-47 で EPS・配当・業績予想を足した。これ以外の科目は確認なく足さない。

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

代表 local_name は例であり網羅ではない。実装後 `report-unmapped` を `fact_count DESC` で出し、上位の標準タクソノミ要素を実装者へ報告する。**エージェントが独断でマッピングを足さない。**

TDnet 短信 ZIP には BS/CF が薄い（PL 中心のサマリ）ものが多い。欠損は正常。`is_required` は売上・営利・純利・資産合計など、短信に普通あるものだけ 1 にする。

### 7.3 IFRS・業種

- IFRS は `jpigp_cor` 等。`accounting_standard='IFRS'` のマッピングは filing の基準一致時のみ。ただし要素名が `*IFRS` なら filing の基準が JPGAAP / NULL でも使う（KAB-55 / 40530）
- IFRS の書類では `jppfs_*` を正規化に使わない。日本の制度上、単体決算書は日本基準のため IFRS 短信に両基準の本表が同居することがある（436A0）。IFRS 側に対応が無い科目は空欄
- IFRS に経常利益は無い。欠損はエラーにしない
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
