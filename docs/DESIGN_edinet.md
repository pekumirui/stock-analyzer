# EDINET 取込 設計

EDINET API v2（金融庁）の提出者・書類一覧と、有報・半期報の財務 XBRL を `stock.db` に載せる。親 issue は KAB-71。§1〜3 は KAB-72（土台）、§4 は KAB-73（財務 XBRL）、§5 は KAB-74（大量保有）、§6・§7 は KAB-79（自己株買い・公開買付）。有報テキスト（KAB-75）は、`edinet_documents` を入口にして節を足す。

## 書類種別の早見表

仕様書は金融庁の「EDINET API 仕様書（Version 2）」（2026 年 6 月版で確認。https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf 。PDF はコミットしない）。コードの定数は `app/edinet_wrapper/enums.py` の `DocType`。下の表は取り込んでいる書類だけ。「実物」は実データの書類で中身・一覧の値を確かめたか（未確認の種別は仕様書とラッパーの名前だけ）。

| コード | 書類 | 取込先 | 中身 | 閲覧期間 | parentDocID（親） | subjectEdinetCode | 実物 |
|---|---|---|---|---|---|---|---|
| 120 / 130 | 有価証券報告書 / 訂正 | `xbrl_filings`（§4） | type=1 XBRL | 10 年 | 訂正は訂正前 | — | 済 |
| 140 / 150 | 四半期報告書 / 訂正（2024-03 まで） | 同上 | type=1 | 10 年 | 同上 | — | 済 |
| 160 / 170 | 半期報告書 / 訂正 | 同上 | type=1 | 10 年 | 同上 | — | 済 |
| 220 / 230 | 自己株券買付状況報告書 / 訂正 | `edinet_buybacks`（§6） | type=1 のテキストブロック（CSV は表がつぶれる）。要素が無い書類は本文 iXBRL の表（§6） | 1 年 | 訂正は訂正前 | — | 済 |
| 240 | 公開買付届出書 | `edinet_tender_offer_docs`（§7） | type=1 のテキストブロック | 5 年 | 無し（案件の大元） | 対象者 | 済 |
| 250 | 訂正公開買付届出書 | 同上 | type=1（訂正後の全文） | 5 年 | 240 | 対象者 | 済 |
| 260 | 公開買付撤回届出書 | 同上 | 読まない（メタデータだけ） | 5 年 | 240 | 対象者 | 未確認 |
| 270 / 280 | 公開買付報告書 / 訂正 | 同上 | type=1 | 5 年 | 270 は 240、280 は訂正前（270）のはず | 対象者 | 270 済・280 未確認 |
| 290 | 意見表明報告書 | 同上 | type=1 | 5 年 | 240 | **買付者**（提出者が対象者） | 済 |
| 300 | 訂正意見表明報告書 | 同上 | type=1（意見の要素名が 290 と違う書類あり。§7） | 5 年 | **290**（240 ではない） | 買付者 | 済 |
| 310 / 320 | 対質問回答報告書 / 訂正 | 同上 | 読まない | 5 年 | 240（訂正は訂正前のはず） | — | 未確認 |
| 330 / 340 | 別途買付け禁止の特例を受けるための申出書 / 訂正 | 同上 | 読まない | 5 年 | — | — | 未確認 |
| 350 / 360 | 大量保有報告書・変更報告書 / 訂正 | `large_holding_reports`（§5） | type=5 CSV | 5 年 | 変更報告書は基の報告書、訂正は訂正前 | — | 済 |

- 仕様書の親書類管理番号: 訂正報告書は訂正前の書類、公開買付報告書・撤回届出書・対質問回答報告書・意見表明報告書は報告対象の届出書（3-1 の「親書類管理番号」）。300 の親が 290 なのはこの「訂正前の書類」のほう。どれも「提出操作上設定されている場合のみ」で、空のこともある
- 仕様書の `subjectEdinetCode` は「公開買付けについて対象となる EDINET コード」とだけある。意見表明（290/300）では買付者のコードが入る（実データ。§7）
- 自社株 TOB（発行者による公開買付け）も同じ 240〜280 で、府令コード 050・様式 `jptoi` で見分ける。subject は自分
- 閲覧期間（縦覧期間＋延長期間）を過ぎた書類は一覧 API からも消え、書類も取れない。延長期間があるのは有報・半期報・四半期報だけ（仕様書 1-2-2）。自己株買い 1 年・公開買付と大量保有 5 年は法定の縦覧期間で、一覧からの消え方も実データで確かめた（§6・§7）

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
- ラッパーが受け持つこと: HTTP 200 で返るエラーの判定（Content-Type と `metadata.status`）、429・5xx・メンテ画面のリトライ、リクエスト間の最小 1 秒、ZIP の安全な展開、`Subscription-Key` のマスク。HTML 応答と 302 は `EdinetMaintenanceError`（ジョブ側の扱いは §8）
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
- 前期の値やほかの軸が要るようになったら、`keep_fact` を広げて `--reparse`。KAB-78 の有報の科目（従業員・株式数・主要な経営指標等など。`DESIGN_xbrl.md` §7.2）は今の `keep_fact` で残っている。入れるのは `load-mappings` → `xbrl_normalize.py --rebuild` だけ

### 正規化と TDnet との関係

- マッピングは TDnet と同じ `config/xbrl/mappings/*.yaml`。EDINET 用の分岐は無い
- 同じ決算期の実績が TDnet（短信）と EDINET（有報）の両方に入る。`xbrl_financials` は filing ごとなので重ならない。`statements` は上書きしない
- 修正履歴（KAB-57、`app/xbrl/revisions.py`）は `source='tdnet'` だけ読む。有報の実績が提出日にもう一度出ないように
- 実データ（2025-06-27 の有報、2025-11-14 の半期報）で、日本基準は 22 科目が取れ、IR Bank の値（百万円）と一致した。IFRS は KAB-73 で本表の 14 要素を足した（`config/xbrl/mappings/ifrs.yaml` 末尾。承認済み）
- 容量: 有報 1 件約 46KB（fact・context・正規化）。10 年分（有報・半期報・四半期報）で数 GB 規模
- セグメント（KAB-77、`docs/DESIGN_xbrl.md` §14）で 1 件あたり有報 +約 16KB、半期報・四半期報 +約 12KB（fact +50 / context +14 / `xbrl_segments` +16 行ほど。2025 年の 17 件で計測）。10 万件で +1.5GB 前後

## 5. 大量保有報告書（KAB-74）

`edinet_documents` の大量保有報告書・変更報告書（書類種別 350）と訂正報告書（360）を、書類取得 API type=5（XBRL→CSV）で取る。書類 1 件 = `large_holding_reports` 1 行、提出者・共同保有者 1 人 = `large_holdings` 1 行。

```sql
large_holding_reports   -- 1 書類
  doc_id PK
  file_date, submit_datetime, doc_type_code, form_code, title
  filer_edinet_code, filer_name              -- 提出者（連名なら代表）
  issuer_edinet_code, issuer_sec_code, issuer_name
  code                                       -- stocks.code。上場していなければ NULL。索引 (code, obligation_date)
  obligation_date                            -- 報告義務発生日
  filing_date, reason, arrangement, holder_count
  shares_held, outstanding_shares, holding_ratio, holding_ratio_prev   -- 合計（提出者及び共同保有者）
  joint_holders_text                         -- 第3 共同保有者に関する事項（文章のまま。該当なしは NULL）
  is_amendment, amends_doc_id, original_doc_id, is_latest, withdrawn
  parse_status, parse_error, fetched_at

large_holdings          -- 1 書類 × 1 人
  doc_id, holder_no PK                       -- holder_no は CSV の FilerLargeVolumeHolder<N>Member の N
  edinet_code, name, holder_type
  shares_held, outstanding_shares, holding_ratio, holding_ratio_prev
  base_date, purpose, important_proposal
  own_funds, borrowings, other_funds, total_funds   -- 取得資金（円）
```

- ジョブ: `python jobs/edinet/edinet_large_holdings.py`。cron は毎時 09:05〜23:05 に `--limit 1000`（1 件約 1 秒で約 17 分。一覧の :40 と XBRL の :50 に重ねない）
- 対象: 350 / 360・`csv_flag`、取下げ（`withdrawal_status=2`）・不開示・修正通知の行を除く。発行者が上場していない書類も取る（`code` は NULL）。新しい日から。`parsed` は飛ばす（`--reparse` で取り直し）、`failed` は `--retry-failed`。CSV が 404（取り下げ・非公開・取得可能期間外）なら `parse_status='failed'`（`parse_error` に 404 か `outside-edinet-window`）として残し、次の回は飛ばす。ログは `missing n=` に集約（1 件ごとの `download-failed doc_id=` は出さない。Slack の `failed doc_id=` に当たるため）。`errors` にも `failed=` にも数えない。cron は exit=0。5xx・タイムアウトは行を作らず次の回にまた取る（`errors`、exit=1。ログは `download-error n=`）
- EDINET は大量保有を 5 年分しか持たない（一覧の書類メタは 10 年分残るが type=5 は期限切れで 404）。`pending_documents` は `file_date` が 5 年より前を除く。report が無い期間外はダウンロードせず `mark_outside_window` で failed に残す。1 日 20〜50 件、年 1 万件ほど。全部で 5 万件前後、1 件 1KB 前後で数十 MB。初回は毎時の cron で 4 日ほどかかる
- 値: 割合は小数（`0.0507` = 5.07%）。CSV の「－」は NULL。保有者が 1 人の書類は書類レベルの合計が CSV に無いので、保有者 1 の値を合計に入れる。「該当事項なし」の文章（共同保有者・重要提案行為等）は NULL
- 発行者の `code` は `issuer_edinet_code` → `edinet_filers.sec_code`、無ければ CSV の発行者の証券コード（4 桁 → 末尾 0 の 5 桁）。どちらも `stocks` にあるときだけ付ける。`edinet_documents.code` は提出者（保有者）の銘柄なので使わない
- 共同保有者: 連名の書類は全員が `large_holdings` に並ぶ（`holder_count` = 人数）。連名でない共同保有者は「第3 共同保有者に関する事項」の文章にしか無いので `joint_holders_text` に入れる。2021-11 と 2026-10 の 101 件では、文章に名前があるものは無かった
- 訂正: 訂正報告書（360）の CSV は訂正後の全文（大量保有報告書・変更報告書の様式そのまま）。訂正の対象は DEI の `IdentificationOfDocumentSubjectToAmendmentDEI`（無ければ一覧の `parentDocID`）。DEI が docID の形（S + 英数字 7）でないときは `parentDocID` を使う（末尾を推測しない）。訂正の訂正もたどって大元を `original_doc_id` に入れる（訂正でなければ自分）
- 最新: 取込のたびに全件から決め直す（`refresh_versions`。`--refresh-only` で手でも）。`original_doc_id` ごとに、`parsed` で取下げ・不開示でない書類のうち `submit_datetime` が新しい 1 件が `is_latest`。取下げ・不開示は `edinet_documents` の状態から `withdrawn` に入れ、行は消さない
- 使う側: 銘柄の大量保有の推移は `WHERE code = ? AND is_latest ORDER BY obligation_date`。保有者ごとの最新は、そこから `large_holdings.edinet_code` ごとに最後の行
- 実データ（2026-10-15 の 52 件、2021-11-15 の 47 件）で全件パースでき、3 件を閲覧画面の本文（XBRL の honbun）と突き合わせて、保有割合・直前の割合・保有株数・発行済株式等総数・報告義務発生日が一致した
- 画面は保有者タブ `docs/DESIGN_holders.md` と銘柄の株主タブ `docs/DESIGN_stock.md`。API は読み取り（`GET /api/holders/*` と `GET /api/stock/{code}/holders`）

## 6. 自己株券買付状況報告書（KAB-79）

`edinet_documents` の自己株券買付状況報告書（書類種別 220）と訂正（230）を、書類取得 API type=1（XBRL）で取る。書類 1 件 = `edinet_buybacks` 1 行、決議 1 件 = `edinet_buyback_resolutions` 1 行。

```sql
edinet_buybacks              -- 1 書類
  doc_id PK
  file_date, submit_datetime, doc_type_code, form_code   -- 170000 株式会社 / 253000 投資法人
  code, edinet_code, filer_name                          -- code は stocks.code。索引 (code, report_month)
  report_month                                           -- YYYY-MM（報告期間の末日の月）
  period_start, period_end                               -- 表紙の報告期間
  issued_shares, treasury_shares                         -- ３【保有状況】の報告月末
  resolution_count, month_shares, month_amount           -- 報告月の取得（決議の合計）
  is_amendment, amends_doc_id, original_doc_id, is_latest, withdrawn
  parse_status, parse_error, fetched_at   -- parsed / meta（取得状況が対象外の訂正）/ failed

edinet_buyback_resolutions   -- 1 書類 × 決議 1 件
  doc_id, resolution_no PK
  kind                          -- board（取締役会・投資法人の役員会）/ shareholders（株主総会）
  body, resolution_date         -- 決議した機関と決議日
  acquire_from, acquire_to      -- 取得期間（「取得日 A」は from = to）
  limit_shares, limit_amount    -- 決議の枠
  month_shares, month_amount    -- 報告月の取得（「－」は 0）
  cumulative_shares, cumulative_amount   -- 報告月末までの累計
  progress_shares, progress_amount       -- 進捗（書類の値。0.5297 = 52.97%）
  label                         -- 決議状況の行の見出し（そのまま）
```

- 中身の取り方を実データで比べた（2026-07〜10 の 220/230 約 2,100 件のうち 92 件）。CSV（type=5）は表紙と DEI のほかはテキストブロックだけで、表がつぶれて 1 行の文になる（`4,000,00010,000,000,000`）。XBRL のインスタンスのテキストブロックは HTML の表のまま入っているので、そちらを読む（`app/edinet/textblocks.py`）。PDF は使わない
- 読むテキストブロック: `AcquisitionsByResolutionOfShareholdersMeetingTextBlock` / `AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock`（投資法人は `AcquisitionsOfTreasurySharesTextBlock`）と `HoldingOfTreasurySharesTextBlock`。報告期間は表紙の `ReportingPeriodCoverPage`。取得状況の要素がインスタンスに無い書類（S100Z1HS）は本文 iXBRL（`honbun *_ixbrl.htm`）の「１【取得状況】」〜「２【処理状況】」手前の未タグ表を同じ見出しで読む。株主総会/取締役会の見出しが無い、単位が株・円でない（千株・千円など）、累計>枠・報告月>累計・進捗率が累計÷枠と 0.5pt 超ずれる、同じ区分・同じ決議日が重複、取得状況の本文が複数、決議が取れず「該当事項はありません」でもない、は parse_error（換算や推測をしない）。訂正で取得状況が訂正事項に無く章も無いのは例外（`meta`）
- 表は行の見出しで読む: 「決議状況」の行で新しい決議を始め（枠は右の 2 セル）、「計」が報告月の取得、「累計」「累積」が累計、「進捗」が進捗率。計の行が無い書類（S100YUHP。取得日 1 日で報告月の行に実数）は報告月の行を当月にする。計が空欄（「―」や空）で日付の内訳がある書類（S100YBMN）は内訳の合計を当月にする。計が累計を超え、日付の内訳が無く、報告月の行が累計を超えない書類（S100Y8WI。計の金額の誤記）は報告月の行を当月にする。累計が 0 で当月が正、進捗が当月÷枠と 0.5pt 以内で合う書類（S100XZ69。同月消却で累計が 0）は当月も累計も書類の値のまま。「－」は 0。計がある通常の表は報告月の行と日付の内訳を足さない。計が無く内訳がある、計も報告月も累計を超える（進捗が当月÷枠と合わない累計 0 を含む）、は parse_error。1 書類に決議が 2 件以上ある（終わりかけと始まったもの）。取得期間が決議の行の下の行にある書類、「2026年９月末日」、枠が空欄の書類（2 件。書類の記載どおり NULL）がある
- 処理状況（２【処理状況】）・取得日ごとの行は持たない
- ジョブ: `python jobs/edinet/edinet_buybacks.py`。cron は毎時 09:25〜23:25 に `--limit 500`（1 件約 1 秒で約 9 分）。parse 失敗は 1 件ずつ stderr に `parse-error doc_id=` と例外の型・メッセージ・短いトレースを出す（cron-run の 2>&1 で `batch_logs` に載る）。`failed doc_id=` は出さない（Slack の batch パターンに当たる）。鍵・トークンは伏せる
- 対象: 220 / 230・`xbrl_flag`、取下げ・不開示・修正通知の行を除く。新しい日から。`parsed` / `meta` は飛ばす（`--reparse`）、`failed` は `--retry-failed`。404 と期間外は `missing`（failed で残し、exit=0）、5xx・タイムアウトは行を作らず次の回（exit=1）。大量保有（§5）と同じ
- 縦覧期間は 1 年。過ぎた書類は EDINET の一覧からも消え、取れない（2026-10-22 に 2025-10-20 の一覧を取ると 220 は 0 件、2025-11-04 は 43 件）。`file_date` が 1 年より前は `mark_outside_window` で failed に残す。1 日 30〜70 件、年 7,000 件ほど。1 件 1KB 前後
- 訂正: 230 の XBRL は原則として訂正後の全文。訂正の対象は DEI の `IdentificationOfDocumentSubjectToAmendmentDEI`（無ければ `parentDocID`）。DEI が docID の形（S + 英数字 7）でないときは `parentDocID` を使う（S100YWV5 の DEI はヘアスペース付きの `S100YQ5` で末尾が無い。推測で補わない）。大元・取下げ・最新は大量保有と同じ（`refresh_versions`、`--refresh-only`）。表紙の【提出書類】が「訂正報告書」、または DEI の `AmendmentFlagDEI` / 書類種別 230 の訂正で、【訂正事項】に取得状況が含まれず「１【取得状況】」の章も無い書類（S100YWV5）は取得値を持たない `meta` で終える（0 件にしない。最新にもしない）。訂正事項に取得状況があるのに章が無い、または通常の報告書（220）で章が無い、は今までどおり parse_error。【訂正事項】は type=1 ZIP の `PublicDoc/*.htm` を見る（iXBRL とは限らない。S100YWV5 は `PublicDoc/0001000_*.htm` に「２ 【訂正事項】」「２　処理状況」「３　保有状況」。本文 iXBRL と DEI には無い）。取得状況の章はこれまでどおり本文 iXBRL（`honbun *_ixbrl.htm`）だけを見る
- 使う側: 銘柄の月ごとの自己株買いは `WHERE code = ? AND is_latest ORDER BY report_month`。決議ごとの進み具合は、`edinet_buyback_resolutions` を `resolution_date` で束ねて月をまたいで並べる
- 実データ（2026-10-01・02・14 と無作為の 80 件）で全件パースでき、累計 ÷ 枠と書類の進捗率が全件で合った（差 0.2% 以内）。3 社（メルカリ S100Z824、りそな HD S100Z5JO、堺化学 S100Z5K9 と訂正 S100Z5L3）を閲覧画面の本文と突き合わせて、決議日・取得期間・枠・報告月の取得・累計・進捗率・発行済株式総数・保有自己株式数が一致した。堺化学の元の報告書は報告期間が「2026年8月1日〜」で、訂正で 9 月 1 日に直っている

## 7. 公開買付け（KAB-79）

`edinet_documents` の公開買付けの書類（書類種別 240〜340）を取る。書類 1 件 = `edinet_tender_offer_docs` 1 行、案件 1 件 = `edinet_tender_offers` 1 行。

| 種別 | 書類 | 読むもの |
|---|---|---|
| 240 / 250 | 公開買付届出書 / 訂正届出書 | 期間、価格（普通株式 1 株。投資法人は 1 口）、買付予定数・下限・上限、買付け後の所有割合、対象者名 |
| 260 | 撤回届出書 | 一覧のメタデータだけ |
| 270 / 280 | 公開買付報告書 / 訂正報告書 | 期間、成否、応募数・買付数（株式換算）、買付け後の所有割合 |
| 290 / 300 | 意見表明報告書 / 訂正 | 意見の内容（文）と、賛同・応募推奨の分類 |
| 310〜340 | 対質問回答報告書、別途買付け禁止の特例を受けるための申出書（と訂正） | 一覧のメタデータだけ |

```sql
edinet_tender_offer_docs     -- 1 書類
  doc_id PK
  tender_offer_id            -- 大元（parent_doc_id を届出書までたどる）。索引
  file_date, submit_datetime, doc_type_code, ordinance_code, form_code, title
  filer_edinet_code, filer_name, subject_edinet_code, parent_doc_id, withdrawn
  period_start, period_end, price, planned_shares, min_shares, max_shares, holding_ratio_after, target_name
  opinion, recommendation, opinion_text                 -- 意見表明
  success, tendered_shares, purchased_shares            -- 報告書
  parse_status                                          -- parsed / meta（読まない書類）/ failed
  parse_error, fetched_at

edinet_tender_offers         -- 1 案件。取込のたびに全件から作り直す
  tender_offer_id PK         -- 大元の docID（ふつうは 240）
  is_issuer                  -- 発行者による公開買付け（自社株 TOB。府令コード 050）
  offeror_edinet_code, offeror_name, offeror_code       -- 買付者。上場していれば stocks.code
  target_edinet_code, target_name, target_code          -- 対象者。索引
  announced_date             -- 届出書の提出日
  period_start, period_end   -- 期間（延長したら延長後。報告書があれば報告書の期間）
  price, price_initial       -- いまの価格と届出書の当初の価格（引き上げが分かる）
  planned_shares, min_shares, max_shares, holding_ratio_after
  opinion, recommendation    -- いちばん新しい意見表明
  status                     -- open / succeeded / failed / withdrawn
  tendered_shares, purchased_shares, result_date
  latest_doc_id, doc_count, updated_at
```

- 中身は書類取得 API type=1（XBRL）のテキストブロック。CSV（type=5）の数値の要素は議決権の数と所有割合だけで、価格・期間・予定数・成否・意見はテキストブロックにしか無い（§6 と同じく CSV では表がつぶれる）
- 様式: 他社株（府令 040）は `jptoo-ton`（届出書）/ `jptoo-tor`（報告書）/ `jptoo-pst`（意見表明）、発行者（050）は `jptoi`。発行者の届出書は「下限」が無く「超過予定数」と「計」の列。上限は「計」、成否の欄が無いので買付数 > 0 を成立とする
- 訂正届出書（250）の XBRL は訂正後の全文（表紙の書名が「公開買付届出書（…付け訂正届出書の添付インラインXBRL）」）。案件の値は新しい届出書・訂正から順に、値のある最初の書類を使う
- 束ね方: 250 / 260 / 270 / 290 の `parentDocID` は届出書（240）、300 は意見表明（290）。`parent_doc_id` をたどって 240 を大元にする。親が DB に無ければ親の docID を大元にする（同じ親の書類は同じ案件）。届出書も意見表明も取込の範囲より前だと、300 の系統（親 290）と 250・270 の系統（親 240）が別れるので、大元が DB に無い案件同士は買付者・対象者が同じなら束ねる（`_merge_orphans`。大元は 240 の系統の方）。書類情報修正があれば修正の行のメタデータ（対象者の付け替えなど）を使う
- 買付者・対象者: 届出書・報告書は提出者が買付者、`subjectEdinetCode` が対象者。意見表明は逆（提出者が対象者、`subjectEdinetCode` が買付者）。発行者の TOB は両方とも自分。`stocks.code` は `edinet_filers.sec_code` か意見表明の `secCode` から引く。上場していない買付者は名前だけ
- 意見の分類（`classify_opinion`）: 「（１）意見の内容」の文から（【】なしの「(1) 本公開買付けに関する意見の内容」も含む）。テキストブロックは `OpinionRegardingSaidTenderOfferAndBasisAndReasonsEtcTextBlock`、無ければ `OpinionAndBasisAndReasonOfOpinionRegardingSaidTenderOfferTextBlock`（訂正 S100Y50I）。opinion は 賛同しない・反対 → `oppose`、賛同 → `support`、留保 → `reserved`、中立 → `neutral`。「賛同しつつ、応募は中立の立場で株主の判断に委ねる」が多いので、中立より賛同を先に見る。recommendation は 応募しないことを推奨 → `not_recommend`、応募を推奨 → `recommend`、判断に委ね → `leave`
- 成否（`parse_success`）: 「不成立」「全部の買付け等を行いません」を先に見て、次に「成立」「買付け等を行います」。不成立の買付数（「―」）は 0
- 状態: 撤回届出書があれば `withdrawn`、報告書があれば成否で `succeeded` / `failed`、無ければ `open`。取下げ・不開示の書類は案件に数えない（`withdrawn` の書類として残す）
- ジョブ: `python jobs/edinet/edinet_tender_offers.py`。cron は毎時 09:20〜23:20 に `--limit 100`（届出書は 1 件 1〜3 秒で 5 分以内。:25 の自己株買いに重ねない）。404・期間外・5xx の扱いは §5 と同じ。`--refresh-only` で案件だけ作り直す
- 縦覧期間は 5 年（2026-10-22 に 2021-10-01 の一覧は 240 が 2 件、2021-09-01 は 0 件）。1 日 0〜5 件、5 年で 2,500 件ほど
- 使う側: 銘柄が対象の TOB は `edinet_tender_offers WHERE target_code = ?`。価格・期間の変わり方は `edinet_tender_offer_docs WHERE tender_offer_id = ? AND doc_type_code IN ('240','250') ORDER BY submit_datetime`
- 実データ（2026-07-01〜10-21 の 127 件）で全件パースでき、届出書の期間・価格・予定数、報告書の応募数・成否、意見表明の分類が全件で埋まった（意見は賛同＋推奨 35、賛同＋委ねる 9。不成立 1）。3 件（カカクコム S100Y3N0 系の 250 と 300、シード S100YZ5A 系の 270 と 290、伊藤忠商事の自社株 TOB S100YU44 と 270）を閲覧画面の本文と突き合わせて一致した。カカクコムは訂正 9 回で価格 3,000 → 3,682 円、期間 7/16 → 10/28 の延長を追えた
- 通しで（2026-07-01〜10-21 の一覧 → 両ジョブ）案件 36 件・書類 127 件、自己株買い 10 月分 517 件が失敗 0 で入り、2 回目は 0 件（重複しない）。範囲の先頭では届出書が無い案件（届出書の日付・当初価格が NULL）や、報告書だけの案件が出る
- 画面と API はまだ出さない（別 issue）

## 8. 運用

- cron は `deploy/crontab`（07:30 コードリスト、09:05〜23:05 毎時 大量保有、09:20〜23:20 毎時 公開買付、09:25〜23:25 毎時 自己株買い、08:40〜23:40 毎時 一覧、08:50〜23:50 毎時 XBRL）。ログは `/var/log/app/edinet_codes.log` / `edinet_documents.log` / `edinet_xbrl.log` / `edinet_large_holdings.log` / `edinet_tender_offers.log` / `edinet_buybacks.log`（MCP `batch_logs`）
- EDINET がメンテ画面（HTML または案内への 302）を返すと、ラッパーが `EdinetMaintenanceError` にする。ジョブは 1 件目で打ち切り、`WARN edinet maintenance skip` 1 行で exit 0（KAB-149。`edinet_codes` は httpx 直叩きなので対象外）。5xx・タイムアウトは今までどおり exit=1。半日以上メンテが続いたときの昇格はしない（次回 cron が取り直す）
- MCP `db_stats` に `edinet_documents` / `edinet_list_days` の件数と範囲、`large_holding_reports` / `large_holdings` / `edinet_buybacks` / `edinet_buyback_resolutions` / `edinet_tender_offers` / `edinet_tender_offer_docs` の件数が出る
- 初回の本番投入: デプロイ（`jobs/migrate.py` が表と `stocks.edinet_code` を足す）→ `python jobs/edinet/edinet_codes.py` → `python jobs/edinet/edinet_documents.py --backfill-years 10`（約 1 時間）→ IFRS マッピングの追加を TDnet 分にも効かせる `python jobs/xbrl/xbrl_parse.py load-mappings` と `python jobs/xbrl/xbrl_normalize.py --rebuild`。XBRL は cron に任せる（急ぐなら `python jobs/edinet/edinet_xbrl.py --limit 0` を手で）
- 大量保有の初回: デプロイ（`jobs/migrate.py` が表を足す）のあとは cron に任せる。急ぐなら `python jobs/edinet/edinet_large_holdings.py --limit 0` を手で（5 万件で約 14 時間）
- 自己株買い・公開買付の初回: デプロイ（`jobs/migrate.py` が表を足す）のあとは cron に任せる。自己株買いは 1 年分約 7,000 件で 14 時間ほど、公開買付は 5 年分約 2,500 件で 1 日ほど。急ぐなら `--limit 0` を手で
