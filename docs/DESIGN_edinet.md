# EDINET 取込 設計

EDINET API v2（金融庁）の提出者・書類一覧と、有報・半期報の財務 XBRL を `stock.db` に載せる。親 issue は KAB-71。§1〜3 は KAB-72（土台）、§4 は KAB-73（財務 XBRL）。大量保有（KAB-74）と有報テキスト（KAB-75）は、`edinet_documents` を入口にして節を足す。

## 0. 決めたこと

1. ラッパーは自作の `edinet-wrapper`（https://github.com/pekumirui/edinet-api 、private）を `app/edinet_wrapper/` に同梱する。理由: private なので VPS・CI・クラウドのエージェントから `pip install git+…` できない。`app/xbrlp/` と同じ扱い（§1）
2. 書類一覧の一意キーは `(file_date, seq_number)`。`docID` は一意でない（書類情報修正・不開示の開始/解除・取下げが別レコードで出る）
3. `stocks.edinet_code` を nullable で足す（`DESIGN_xbrl.md` §0-2 を更新）。企業キーは今後も `stocks.code`
4. 一覧は全書類（投信・非上場・大量保有を含む）を持つ。上場銘柄に絞らない。大量保有の保有者や関係会社は非上場の提出者だから
5. UI・API はまだ出さない

## 1. ラッパー

- 置き場は `app/edinet_wrapper/`。upstream の `src/edinet_wrapper` @ `a3a2f01` の写し。変えたのは `from edinet_wrapper.x` → `from .x` だけ（`__init__.py` の docstring に書いた）。Ruff / Bandit の対象から外す
- 更新するときは upstream の `src/edinet_wrapper/*.py` をそのまま写して、同じ置換をかける:
  `sed -i -E 's/^(\s*)from edinet_wrapper\./\1from ./' app/edinet_wrapper/*.py`
- 依存は `httpx` と `pydantic` v2（どちらも `requirements.txt` にある）
- Python 3.11+ が要る（`StrEnum` / `typing.Self`）。アプリ本体は 3.10 でも起動するよう、`app/edinet/` は型以外のラッパーを関数の中で import する
- ラッパーが受け持つこと: HTTP 200 で返るエラーの判定（Content-Type と `metadata.status`）、429・5xx・メンテ画面のリトライ、リクエスト間の最小 1 秒、ZIP の安全な展開、`Subscription-Key` のマスク
- キーは `EDINET_API_KEY`（`.env` か環境変数）。本番は GitHub Secrets → `deploy/update.sh` が `.env` に書く。無ければ一覧のジョブは何もしない

## 2. 提出者（`edinet_filers`）と `stocks.edinet_code`

```sql
edinet_filers      -- EDINET コードリスト（Edinetcode.zip、キー不要）の写し
  edinet_code PK   -- E00004 など
  sec_code         -- 証券コード 5 桁（stocks.code と同じ形）。無い提出者は NULL
  filer_name, filer_name_en, filer_type, listed, is_consolidated,
  capital, fiscal_year_end, industry, jcn
  list_date        -- リスト 1 行目の「ダウンロード実行日」
  updated_at

stocks.edinet_code -- nullable。jobs/edinet/edinet_codes.py が付け直す
```

- ジョブ: `python jobs/edinet/edinet_codes.py`（`--zip` で手元の ZIP）。cron は毎日 07:30
- `edinet_filers` は UPSERT。リストから消えた提出者も残す（古い書類の提出者名を引くため）
- 紐付けは `edinet_filers.sec_code = stocks.code`。同じ証券コードに提出者が複数あれば「上場」、次に `list_date` が新しい方
- 5 桁目が 0 でない銘柄（優先株式。`92025` など）は普通株（`92020`）の提出者を付ける
- リストから外れた銘柄は NULL に戻す
- TOKYO PRO MARKET は EDINET に出さないので、付かなくて正しい。ログの `unmatched_equity` に数えない
- 2026-10-20 の実データ: 提出者 11,420、証券コード付き 3,813。TOKYO PRO MARKET を除く内国株で付かなかったのは 4 件（コードリストに証券コードがまだ無い新規上場など）。翌日以降のリスト更新で付く
- 取り込んだあと、`edinet_documents.code` が空の行を付け直す（`relink_codes`）

## 3. 書類一覧（`edinet_documents` / `edinet_list_days`）

```sql
edinet_documents   -- 書類一覧 API type=2 の 1 行
  file_date, seq_number PK
  doc_id           -- 索引。一意でない
  code             -- stocks.code。secCode、無ければ edinet_code → edinet_filers.sec_code。上場していなければ NULL
  edinet_code, sec_code, jcn, filer_name, fund_code,
  ordinance_code, form_code, doc_type_code, period_start, period_end,
  submit_datetime, doc_description,
  issuer_edinet_code, subject_edinet_code,     -- 大量保有・公開買付けの発行者 / 対象
  subsidiary_edinet_codes, current_report_reasons,  -- カンマ区切り
  parent_doc_id, ope_datetime,
  withdrawal_status, doc_info_edit_status, disclosure_status,  -- EDINET のコード値（'0'〜'3'）
  xbrl_flag, pdf_flag, attach_doc_flag, english_doc_flag, csv_flag,
  legal_status, fetched_at
  -- 索引 (code, doc_type_code, submit_datetime)、doc_id、edinet_code、doc_type_code、issuer / subject

edinet_list_days   -- 取った日ごと
  file_date PK, count, max_seq, process_datetime, finalized, fetched_at
```

日時は JST の naive で持つ（EDINET の値は JST）。

### 取り方

- ジョブ: `python jobs/edinet/edinet_documents.py`。1 日 1 リクエスト（type=2。メタデータも入っている）
- 既定（cron）は直近 7 日のうち確定していない日と、それより古い未確定の日。毎時 08:40〜23:40
- 期間指定は `--from` / `--to`。`--backfill-years N`（最大 10）。確定済みの日は飛ばすので、止めても続きから。`--force` で取り直す
- 確定: EDINET の一覧作成時刻（`processDateTime`）が `file_date` の翌日以降の回で取ったら `finalized`。前日分は翌朝の回で確定する
- 過去分は EDINET が 10 年分まで持つ。1 日約 1 秒。1 行は索引込み約 390 バイトで、年 8 万行ほど（2025-06〜2026-04 の実測から）。5 年で約 150MB、10 年で約 300MB。本番は 10 年全部取る（`--backfill-years 10`。約 1 時間）

### 状態の書き換え（イベント行）

EDINET は修正・不開示・取下げを、後の日の一覧に別レコードとして出す。元の日の行は、元の日を取り直さないと変わらない。そこで取り込むたびに、その日の書類（と取下書の親）について、DB にある行全部から状態を決め直す（`apply_events`）。取り直しの順番に依らない。

| イベント行 | 元の行（イベントでない同じ docID の行） |
|---|---|
| 取下書（`withdrawal_status=1`、`parent_doc_id=D`） | D の行を `withdrawal_status=2` |
| 書類情報修正（D に `doc_info_edit_status=1` の行） | `doc_info_edit_status=2`。新しいメタデータは修正の行にある |
| 不開示の開始（`disclosure_status=1`）/ 解除（`3`） | 最後のイベントが開始なら `2`、解除なら `0` |

- 取下げで EDINET は元の行のメタデータを空にして返す。UPSERT は提出者・書類種別などの列を NULL で上書きしない（`_KEEP_ON_NULL`）
- 使う側（KAB-73 以降）は `withdrawal_status != '2' AND disclosure_status != '2'` の行を対象にする。修正があった書類は `ope_datetime` が新しい行のメタデータを使う

## 4. 有報・半期報の財務 XBRL（KAB-73）

`edinet_documents` の書類を書類取得 API type=1（XBRL ZIP）で取り、TDnet と同じ `app/xbrl` でパース・正規化する。`xbrl_filings.source='edinet'`、`doc_id` は EDINET の docID。表は増やさない（`DESIGN_xbrl.md` §4）。

- ジョブ: `python jobs/edinet/edinet_xbrl.py`。cron は毎時 08:50〜23:50 に `--limit 300`（1 件 2〜3 秒で約 14 分。cron-run.sh にロックが無いので 1 時間に収める）
- 対象: `code` あり（上場銘柄）、`xbrl_flag`、取下げ（`withdrawal_status=2`）・不開示（`disclosure_status≠0`）・修正通知の行を除く。書類種別は有報 120 / 訂正 130、半期報 160 / 訂正 170、四半期報告書 140 / 150（2024-03 まで。`--no-quarterly` で除く）。EDINET が持つ 10 年分を全部取る
- 新しい日から取る。一覧を backfill したあとの過去分は、毎時の cron が新着の後に少しずつ取る（1 日約 4,800 件）
- `parsed` / `skipped` の docID は飛ばす（`--reparse` で取り直し）。`failed` は `--retry-failed`。ダウンロードに失敗した書類は filing を作らないので次の回にまた取る
- `doc_type` / `period_type` / `is_amendment` は書類種別コードから決める（120/130 → `annual`/`FY`、160/170 → `semiannual`/`Q2`、140/150 → `quarterly` で期はタイトル）。会計基準は DEI の `AccountingStandardsDEI`（`Japan GAAP` / `IFRS` / `US GAAP`）
- 取り込み済みの書類が後から取下げ・不開示になったら、`xbrl_financials` / `xbrl_segments` と生 fact を消して `parse_status='skipped'`、`parse_error='withdrawn'`
- タクソノミは同梱（`data/xbrl_taxonomy`）だけを読む。10 年分の書類が参照する版を同梱した（`tools/edinet/sample_zips.py --sample-years 10` の見本から `tools/xbrl/taxonomy_bundle.py fetch`）。それでも足りない書類は `failed`（`TaxonomyMissingError`）になる。`sample_zips.py --failed` → `fetch` → `edinet_xbrl.py --retry-failed`

### ZIP と fact は持ちすぎない

- ZIP は保存しない（一時ディレクトリで読んで捨てる）。1 件 0.5〜3MB で、年 8,000 件だと数 GB になる。EDINET が 10 年分持っているので、取り直しはダウンロードし直す。`raw_path` は `edinet:<docID>`、`raw_sha256` はダウンロードした ZIP のもの
- `xbrl_facts` / `xbrl_contexts` には、正規化が使う当期（`relative_period='current'`）・次元なし（連結/単体のメンバーだけは可）の fact、当期のセグメント軸（`jpcrp_cor:OperatingSegmentsAxis`。連結/単体の軸は付いてよい）だけの fact（KAB-77）と DEI だけ入れる（`app/edinet/xbrl.py keep_fact`）。有報は 1 件 1,000〜2,000 fact あり、全部だと年 800MB を超える。絞ると fact は約 4 分の 1、context は 2%。会計基準と期末の判定は絞る前の全 fact で行う
- 前期の値やほかの軸が要るようになったら、`keep_fact` を広げて `--reparse`

### 正規化と TDnet との関係

- マッピングは TDnet と同じ `config/xbrl/mappings/*.yaml`。EDINET 用の分岐は無い
- 同じ決算期の実績が TDnet（短信）と EDINET（有報）の両方に入る。`xbrl_financials` は filing ごとなので重ならない。`statements` は上書きしない
- 修正履歴（KAB-57、`app/xbrl/revisions.py`）は `source='tdnet'` だけ読む。有報の実績が提出日にもう一度出ないように
- 実データ（2025-06-27 の有報、2025-11-14 の半期報）で、日本基準は 22 科目が取れ、IR Bank の値（百万円）と一致した。IFRS は KAB-73 で本表の 14 要素を足した（`config/xbrl/mappings/ifrs.yaml` 末尾。承認済み）
- 容量: 有報 1 件約 46KB（fact・context・正規化）。10 年分（有報・半期報・四半期報）で数 GB 規模
- セグメント（KAB-77、`docs/DESIGN_xbrl.md` §14）で 1 件あたり有報 +約 16KB、半期報・四半期報 +約 12KB（fact +50 / context +14 / `xbrl_segments` +16 行ほど。2025 年の 17 件で計測）。10 万件で +1.5GB 前後

## 5. 運用

- cron は `deploy/crontab`（07:30 コードリスト、08:40〜23:40 毎時 一覧、08:50〜23:50 毎時 XBRL）。ログは `/var/log/app/edinet_codes.log` / `edinet_documents.log` / `edinet_xbrl.log`（MCP `batch_logs`）
- MCP `db_stats` に `edinet_documents` / `edinet_list_days` の件数と範囲が出る
- 初回の本番投入: デプロイ（`jobs/migrate.py` が表と `stocks.edinet_code` を足す）→ `python jobs/edinet/edinet_codes.py` → `python jobs/edinet/edinet_documents.py --backfill-years 10`（約 1 時間）→ IFRS マッピングの追加を TDnet 分にも効かせる `python jobs/xbrl/xbrl_parse.py load-mappings` と `python jobs/xbrl/xbrl_normalize.py --rebuild`。XBRL は cron に任せる（急ぐなら `python jobs/edinet/edinet_xbrl.py --limit 0` を手で）
