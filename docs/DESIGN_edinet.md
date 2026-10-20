# EDINET 取込 設計

EDINET API v2（金融庁）の提出者と書類一覧を `stock.db` に載せる。親 issue は KAB-71。この文書は KAB-72（土台）の分。有報の財務 XBRL（KAB-73）、大量保有（KAB-74）、有報テキスト（KAB-75）は、この表を入口にして節を足す。

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
- 過去分は EDINET が 10 年分まで持つ。1 日約 1 秒。1 行は索引込み約 390 バイトで、年 8 万行ほど（2025-06〜2026-04 の実測から）。5 年で約 150MB、10 年で約 300MB。本番は株価と同じ 5 年を推奨（`--backfill-years 5`。約 30 分）

### 状態の書き換え（イベント行）

EDINET は修正・不開示・取下げを、後の日の一覧に別レコードとして出す。元の日の行は、元の日を取り直さないと変わらない。そこで取り込むたびに、その日の書類（と取下書の親）について、DB にある行全部から状態を決め直す（`apply_events`）。取り直しの順番に依らない。

| イベント行 | 元の行（イベントでない同じ docID の行） |
|---|---|
| 取下書（`withdrawal_status=1`、`parent_doc_id=D`） | D の行を `withdrawal_status=2` |
| 書類情報修正（D に `doc_info_edit_status=1` の行） | `doc_info_edit_status=2`。新しいメタデータは修正の行にある |
| 不開示の開始（`disclosure_status=1`）/ 解除（`3`） | 最後のイベントが開始なら `2`、解除なら `0` |

- 取下げで EDINET は元の行のメタデータを空にして返す。UPSERT は提出者・書類種別などの列を NULL で上書きしない（`_KEEP_ON_NULL`）
- 使う側（KAB-73 以降）は `withdrawal_status != '2' AND disclosure_status != '2'` の行を対象にする。修正があった書類は `ope_datetime` が新しい行のメタデータを使う

## 4. 運用

- cron は `deploy/crontab`（07:30 コードリスト、08:40〜23:40 毎時 一覧）。ログは `/var/log/app/edinet_codes.log` / `edinet_documents.log`（MCP `batch_logs`）
- MCP `db_stats` に `edinet_documents` / `edinet_list_days` の件数と範囲が出る
- 初回の本番投入: デプロイ（`jobs/migrate.py` が表と `stocks.edinet_code` を足す）→ `python jobs/edinet/edinet_codes.py` → `python jobs/edinet/edinet_documents.py --backfill-years 5`
