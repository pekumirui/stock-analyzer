# 取込と保存の規則: EDINET

各ジョブの起動時刻・引数・読み書きするテーブルは自動生成の [ジョブ設計](gen/edinet.md) と [バッチ一覧](../JOBS.md) にまとめる。このページでは金融庁 EDINET API v2 からの書類一覧・ZIP アーカイブの取得と、書類種別ごとのパース・版管理・運用の規則を定める。

## 全体構成と対象書類

EDINET API v2（環境変数 `EDINET_API_KEY`）と金融庁の EDINET コードリスト（API キー不要）を使い、適時開示や J-Quants だけでは取れない法定開示書類を取り込む。財務 XBRL のパースには TDnet の決算短信と同じ `app/xbrlp` を使う。

取り込む書類種別と保存先は次のとおりである。

| `doc_type_code` | 書類種別 | EDINET 縦覧期間 | 取り出す主な内容 | 保存先テーブル |
|---|---|---|---|---|
| `120`・`130` | 有価証券報告書・訂正 | 5 年 | 3 表の財務諸表・研究開発費・設備投資・減価償却費・従業員情報・セグメント・大株主・所有者別状況 | `xbrl_filings`・`xbrl_financials`・`xbrl_segments`・`xbrl_shareholder_*` |
| `140`・`150` | 四半期報告書・訂正（2024 年 4 月廃止） | 5 年 | 過去分の 3 表・セグメント（大株主の表があればそれも含む） | 同上 |
| `160`・`170` | 半期報告書・訂正 | 5 年 | 3 表・セグメント（大株主の表があればそれも含む） | 同上 |
| `220`・`230` | 自己株券買付状況報告書・訂正 | 1 年 | 株主総会・取締役会決議枠、月次の取得株数・総額、処分、月末自己株式数 | `edinet_buybacks`・`edinet_buyback_resolutions` |
| `240`・`250` | 公開買付届出書・訂正 | 5 年 | 買付者・対象銘柄・買付価格・買付期間・下限と上限株数・目的 | `edinet_tender_offers`・`edinet_tender_offer_docs` |
| `260` | 公開買付撤回届出書 | 5 年 | 案件の撤回ステータスへの遷移（一覧メタデータのみ） | 同上 |
| `270`・`280` | 公開買付報告書・訂正 | 5 年 | 応募株数・買付株数・成否・買付後所有割合 | 同上 |
| `290`・`300` | 意見表明報告書・訂正 | 5 年 | 対象会社の意見（賛同・反対・中立・留保） | 同上 |
| `310`・`320`・`330`・`340` | 対質問回答報告書・別途買付け禁止の特例・訂正 | 5 年 | 案件タイムラインへの紐付け（一覧メタデータのみ） | 同上 |
| `350`・`360` | 大量保有報告書・変更報告書・訂正 | 5 年 | 提出者・共同保有者ごとの株券等保有割合・株数・保有目的・重要提案行為等 | `large_holding_reports`・`large_holdings` |

次の書類種別は `edinet_documents` に一覧メタデータだけを残し、本文 ZIP は取得しない。

| `doc_type_code` | 書類種別 | 本文を取得しない理由 |
|---|---|---|
| `010`〜`110` | 有価証券届出書・発行登録書など | 募集・売出し手続きの書類であり、定常的な業績・株主分析に使わないため |
| `180` | 臨時報告書 | 定型の数値タグや表を持たず、事由別の自由記述 `TextBlock` が 1 つ入るだけのため |
| `200`〜`210` | 親会社等状況報告書 | 非上場の親会社に関する開示であり、上場銘柄本体の分析対象外のため |

---

## 共通基盤

### ラッパー

HTTP クライアントには Apache-2.0 ライセンスの `app/edinet_wrapper`（`EdinetClient`）をそのまま同梱して使う（vendor コードのため改変しない）。このラッパーは次の EDINET API v2 特有の挙動を吸収する。

| 項目 | ラッパーの挙動 |
|---|---|
| リトライ | HTTP `429` と `5xx` に対して指数バックオフで再試行する |
| HTTP `200` 内のエラー JSON | EDINET API は書類未検出や認証エラーでも HTTP ステータス `200` のまま JSON 本文 `{"metadata": {"status": "404", ...}}` を返すため、本文の `status` を見て `EdinetNotFoundError` などの例外に変換する |
| ZIP・CSV の検証と展開 | ダウンロード応答の先頭 `PK` シグネチャを検証し、`type=5`（CSV 変換 ZIP）に含まれる UTF-16LE のタブ区切りファイルをデコードする |
| 取得件数の集計 | プロセス内の日次ダウンロード件数（`rate_limit`）を数える |

アプリ側の薄いラッパー `app/edinet/client.py` は、`EDINET_API_KEY` の有無の検査、金融庁メンテナンス時の `503` スキップ処理（後述の「EDINET のメンテ」）、および API キー不要で動く `Edinetcode.zip` 取得ヘルパーを提供する。

### 提出者

対象ジョブは [`edinet/edinet_codes`](gen/edinet.md#edinetedinet_codes)（`jobs/edinet/edinet_codes.py`）である。API キーは不要で、週次 cron で実行する。

金融庁が配布する日本語版 EDINET コードリスト（`EdinetcodeDlInfo.zip`、Shift_JIS の CSV）を取得し、非上場企業・投資信託・外国法人・個人を含む全提出者（約 11,000 件）を `edinet_filers` に UPSERT する。続けて `edinet_filers.sec_code` と `stocks.code` を突き合わせて `stocks.edinet_code` を更新し、`edinet_documents` で `code` が未設定だった行にも `relink_codes` で銘柄コードを埋める。

銘柄コードとの紐付け規則と実データ上の性質は次のとおりである。

| 対象 | 紐付け規則と挙動 |
|---|---|
| 普通株・REIT・インフラファンド | 5 桁の `sec_code` と `stocks.code` の完全一致で引く。同一 `sec_code` に複数の EDINET コードがあるときは、`listed == '上場'`・`list_date` の新しさ・`edinet_code` の順で 1 件を選ぶ |
| 優先株（5 桁目の末尾が `'0'` 以外） | 4 桁の上場コードに `'0'` を付けた普通株コードでも引き当て、発行体法人の EDINET コードを入れる（例: 伊藤園第 1 種優先株式 `25935` → `25930` 経由で `E00416`） |
| コードリストから消えた銘柄 | 今回のコードリストで対応が付かなかった銘柄は `stocks.edinet_code = NULL` に戻す |
| TOKYO PRO Market 上場銘柄 | 法定開示義務が無く EDINET コード自体が存在しないため `NULL` になる（ログの `unmatched_equity` 件数からは TOKYO PRO Market を除外して監視ノイズを防ぐ） |
| ETF・ETN | 個別銘柄の `sec_code` ではなく運用会社の EDINET コードしか持たないため `stocks.edinet_code` は `NULL` になる |

### 書類一覧

対象ジョブは [`edinet/edinet_documents`](gen/edinet.md#edinetedinet_documents)（`jobs/edinet/edinet_documents.py`）である。日次 cron で実行し、各日の `documents.json?date=YYYY-MM-DD&type=2` の全件を `edinet_documents` に保存する。

EDINET の書類一覧では、後日発生した取下げ・不開示措置・メタデータ修正が、その事象の発生日（`file_date`）の一覧に元の書類と同じ `doc_id` で再掲される。元の一覧行を消さずに履歴を保持するため、`edinet_documents` の主キーは `(doc_id, file_date)` の複合キーにしている。

#### 対象日の選び方と確定判定

日次の通常実行では、当日を含む直近 8 暦日（`today - 7` 日〜`today`）と、日次取得台帳 `edinet_list_days` で未確定（`finalized = False`）のまま残っている過去日（最大 `MAX_LOOKBACK_DAYS = 10 * 365 + 7` 日前まで）を取得する。初回の一括取得では `--backfill-years N`（最大 10 年）を指定する。

ある `file_date` の一覧は、API 応答の `processDateTime`（欠けている場合は取得時刻 `fetched_at`）が JST で `file_date` の翌日以降になった回に取得できていれば `finalized = True` にする。当日中に取得した回はまだ駆け込み提出が増える可能性があるため `finalized = False` のまま残し、翌日以降に自動で取り直す。上場廃止済みの過去銘柄も含め、一覧の行自体は捨てずに全件保存する。

#### 銘柄コードの引き当てと事後イベントの伝播

各一覧行の `code` は、まず `sec_code` が `stocks` にあればそれを採用し、無ければ `edinet_code` から `edinet_filers.sec_code` 経由で引く。なお大量保有報告書（`350`・`360`）と公開買付け（`240`〜`340`）の一覧行にある `sec_code` は対象銘柄ではなく提出者自身のコード（ファンドや個人なら `NULL`）であるため、対象銘柄のコードは後続の各パーサーが書類本文から特定する。

取下げ・不開示・メタデータ修正の行が届いたときは、`apply_events` で同じ `doc_id` の過去 `file_date` の行と下流テーブルへ状態を伝播させる。

| 事象 | 一覧での現れ方 | 元の行と下流テーブルへの反映 |
|---|---|---|
| 取下げ | 取下書（`withdrawal_status='1'`）と取り下げられた元書類の通知（`'2'`）が事象日の `file_date` に出る | 過去の元行の `withdrawal_status` を `'2'` に更新し、`edinet_archives` のローカル ZIP を消して `status='gone'` にし、各機能の `retract` で正規化済みデータを引っ込める |
| 不開示・解除 | 不開示開始（`disclosure_status='1'`）・不開示中（`'2'`）・不開示解除（`'3'`）が出る | `'1'`・`'2'` のときは元行の `disclosure_status` を `'2'` にしてローカル ZIP 削除と `retract` を行う。`'3'`（解除）が出たときは元行を `'0'` に戻す |
| メタデータ修正 | 修正通知（`doc_info_edit_status='1'`）と修正された元書類の通知（`'2'`）が出る | 過去の元行の `doc_info_edit_status` を `'2'` に更新する。下流の未処理抽出（`pending_documents`）は元の提出行（`withdrawal_status != '2' AND disclosure_status = '0' AND doc_info_edit_status != '1'`）だけを対象にし、自己株・公開買付けの版更新（`doc_metas`）では `ope_datetime` が最も新しい `'1'` 行のメタデータを参照する |

### 書類の ZIP

EDINET の書類 ZIP は縦覧期間（自己株は 1 年、その他は 5 年）を過ぎると API から `404` で消えるため、一度取得した `type=1`（提出本文・XBRL・監査報告書）と `type=5`（CSV 変換）の ZIP はすべて `app/edinet/archive.py`（`fetch_zip`）経由で保存し、索引テーブル `edinet_archives`（主キー `(doc_id, file_type)`）に `rel_path`・`status`・`bytes`・`sha256`・`saved_at` を記録する。

ローカルの保存先は `data/edinet/{doc_type_code}/{doc_id}_type{file_type}.zip`（環境変数 `EDINET_ARCHIVE_DIR` または `$SHARE_ROOT/edinet` で変更可）である。`fetch_zip` は次の 3 段階のリードスルーで ZIP バイト列を返す。

1. ローカルの `data/edinet` に実ファイルがあり、先頭が有効な ZIP（`PK` ヘッダ）であればそのまま返す。
2. ローカルに無くても `edinet_archives` に `status='saved'` の記録があれば、OneDrive（`{BACKUP_OFFSITE_REMOTE}:{BACKUP_OFFSITE_PATH}/data/edinet/{rel_path}`）から `rclone copyto` でローカルへ書き戻して返す。
3. どちらにも無ければ EDINET API の `client.download` で取得し、ローカル保存と `edinet_archives` への `status='saved'` 記録を行ってから返す。

取下げや不開示になった書類は `mark_gone` でローカルファイルを削除し、`edinet_archives.status = 'gone'` に変更する。またパースとは独立に縦覧期間内の未保存 ZIP をまとめて確保したいときは、専用ジョブ [`edinet/edinet_archive`](gen/edinet.md#edinetedinet_archive)（`jobs/edinet/edinet_archive.py`、既定 `--limit 1000`、`0` で無制限）を使う。

---

## 書類種別ごとの取込規則

### 有報・半期報の財務 XBRL

対象ジョブは [`edinet/edinet_xbrl`](gen/edinet.md#edinetedinet_xbrl)（`jobs/edinet/edinet_xbrl.py`）、表示画面は [銘柄詳細](../screen/stock.md) である。

`edinet_documents` のうち `doc_type_code` が `120`〜`170` であり、`xbrl_flag = True`・`code IS NOT NULL`・`withdrawal_status != '2'`・`disclosure_status = '0'`・`doc_info_edit_status != '1'` を満たし、まだ `xbrl_filings`（`source='edinet'`）に行が無い書類を古い `file_date` 順に処理する（`--retry-failed` 指定時は `parse_status='failed'` も再試行する）。

#### パース・メタデータ・異常時の扱い

`type=1` の ZIP を `fetch_zip` で取得し、`XBRL/PublicDoc/` 配下の XBRL 一式を `app/xbrlp` で読んで `xbrl_filings`（`source='edinet'`・`doc_id` に EDINET 書類管理番号）・`xbrl_documents`・`xbrl_contexts`・`xbrl_facts`・`xbrl_presentation_roles` に入れ、続けて `xbrl_financials`・`xbrl_segments` と大株主テーブル（`normalize_shareholders` および末尾の `refresh_versions`）へ正規化する。実行統計は毎回 `xbrl_job_runs`（`job_name='edinet_xbrl'`）に記録する。

メタデータの変換とエラー時の扱いは次のとおりである。

| 項目 | 規則 |
|---|---|
| `doc_type`・`is_amendment` | `120`・`130` は `annual`、`140`・`150` は `quarterly`、`160`・`170` は `semiannual` にし、`130`・`150`・`170` を `is_amendment = True` にする |
| `period_type` | 有報は `FY`、半期報は `H1`、四半期報は `doc_description` の `第([123])四半期` から `Q1`〜`Q3` を抜く（「第138期」のような期数を `Q1` と誤認するのを避ける） |
| `accounting_standard`・`fiscal_year_end` | DEI の `AccountingStandardsDEI` を `'JPGAAP'`・`'IFRS'`・`'USGAAP'` に正規化して格納し、期末日は `CurrentFiscalYearEndDateDEI`（無ければ `period_end`）を使う |
| 取得時の HTTP `404` | 縦覧期間切れや非公開とみなし、`XbrlFiling` を `parse_status='failed'` で作って `missing` に数える。次回以降は飛ばし、終了コードは `0` のままにする |
| 取得時の `5xx`・タイムアウト・アーカイブ失敗 | `XbrlFiling` を作らずに `errors` に数え、終了コード `1` を返して次回の再取得に回す |
| パース例外・`parse_status='failed'` | `XbrlFiling` を `parse_status='failed'` で残したうえで `errors`・`failed` に数え、終了コード `1` を返す（タクソノミ不足などの異常を監視で検知する） |
| 取下げ・不開示（`retract`） | 該当 filing の `xbrl_financials`・`xbrl_segments`・大株主 3 テーブル・`xbrl_facts` 等のペイロードをすべて削除し、`parse_status='skipped'`・`parse_error='withdrawn'` にする |

#### 1 書類だけ読み直す

`--doc-id <書類管理番号> --reparse` で、その 1 件だけをダウンロードし直して読む（取得は手元の `data/edinet` → EDINET → OneDrive の順）。指紋（`raw_sha256`）は記録するだけで読み直しの判定には使わないので、`--reparse` が要る。既存の filing の行は消さずに使い、`parse_zip_into_filing` がその filing の `xbrl_facts`・`xbrl_contexts`・`xbrl_presentation_roles`・`xbrl_pdf_matches` を消して入れ直し、`normalize_filing` が `xbrl_financials`・`xbrl_segments`・大株主を作り直す。ZIP に別の書類の一式が入っている書類は、先に `config/xbrl/zip_manifest_only.json` に載せる（[XBRL の取込](xbrl.md)。KAB-592）。

```bash
python jobs/edinet/edinet_xbrl.py --doc-id S100W2KO --reparse
```

#### TDnet 短信 XBRL との住み分けと読み出し優先順

TDnet 由来の短信 XBRL（`source='tdnet'`）とは上書きし合わず、同じ銘柄・同じ期でも `xbrl_filings`・`xbrl_financials`・`xbrl_segments` に別々の行として保持する。また決算速報が使う `statements` は発表直後の短信と粒度・タイミングが異なるため、EDINET からは書き換えない。

銘柄詳細の財務 3 表 API（`GET /api/stock/{code}/financial-statements`、`app/xbrl/statements.py`）が複数ソースを束ねるときの規則は次のとおりである。

1. 同じ会計期間の BS・PL・CF は `edinet > tdnet`、同じ `source` 内では訂正あり（`is_amendment = True`）優先、さらに提出日時 `submitted_at` の新しい方を採用する。
2. 最新の決算短信が出た直後でまだ有報・半期報が提出されていない期や、短信側にしか無い項目はそのまま `tdnet` 側の値が出る。
3. 有報でしか開示されない通期 KPI（`xbrl_financials` の研究開発費 `rd_expenses`・設備投資 `capex`・減価償却費 `depreciation`・従業員数 `employees`・平均臨時雇用人員 `average_temp_employees`・平均年齢 `average_age`・平均勤続年数 `average_tenure_years`・平均年間給与 `average_annual_salary`）と、`xbrl_segments` のセグメント別売上高・利益・資産（日本語ラベル優先の `segment_label`）を補完する。

#### 同梱タクソノミの更新手順

金融庁の年度別タクソノミ（`jppfs`・`jpcrp`・`jpdei`・`jpigp`、2013-08-31 版〜2026 年版）は `vendor/taxonomies/edinet/` と `vendor/taxonomies/manifest.json` に同梱している。金融庁が新しい年度のタクソノミを公開して `TaxonomyMissingError` で `failed` になった filing が出たときは、次の手順でスキーマを取り込んで再パースする。

```bash
python tools/edinet/sample_zips.py --failed --out /tmp/edinet-zips
python tools/xbrl/taxonomy_bundle.py fetch --zips /tmp/edinet-zips
python jobs/edinet/edinet_xbrl.py --retry-failed
```

実データでの検証例として、トヨタ自動車（`S100YWV5`、IFRS、2025 年 3 月期有報）で 540 ファクトと 3 セグメント（自動車・金融・その他）、キーエンス（`S100Y50I`、日本基準、2025 年 3 月期有報）で単一セグメントのためセグメント 0 行、任天堂（`S100YEOO`、日本基準、2025 年 3 月期有報）で研究開発費・設備投資・減価償却費・従業員数・平均年間給与が正規化されることを確認している。

### 有報の大株主の状況・所有者別状況

正規化ロジックは `app/xbrl/shareholders.py` にあり、`edinet_xbrl.py` の中で自動的に走るほか、既存の `xbrl_facts` から一括で作り直すときは [`edinet/edinet_shareholders`](gen/edinet.md#edinetedinet_shareholders)（`jobs/edinet/edinet_shareholders.py`）を使う。表示画面は [銘柄詳細](../screen/stock.md) である。

`source='edinet'` の filing から「大株主の状況」と「所有者別状況」を抽出し、ヘッダ `xbrl_shareholder_filings`・大株主明細 `xbrl_major_shareholders`・所有者別明細 `xbrl_shareholding_by_category` に保存する。半期報や四半期報などで両方の表とも存在しない filing ではヘッダ行も作らない。

#### タクソノミ要素と正規化規則

会計基準（日本基準・IFRS・米国基準）を問わず、非財務パートの共通語彙 `jpcrp_cor` から抽出する。

| 対象テーブル | 見る軸・要素と変換規則 |
|---|---|
| `xbrl_major_shareholders` | 軸 `MajorShareholdersAxis`（メンバー `No1MajorShareholdersMember`〜`No30...` または企業拡張の `jpcrp030000-asr_E...` から順位 `rank` を採番）の `NameMajorShareholders`・`AddressMajorShareholders`・`NumberOfSharesHeld`（株）・`ShareholdingRatio` を読む。議決権ベースの表を使う会社向けに `MajorShareholdersVotingRightsAxis`（`...VotingRights` 系 4 要素）にも対応し、両方あるときはメンバー数が多い軸を採用する |
| `xbrl_shareholding_by_category` | 9 区分（政府及び地方公共団体 `gov_local`・金融機関 `financial_inst`・金融商品取引業者 `securities`・その他の法人 `other_corp`・外国法人等計 `foreign_total`・外国法人等のうち個人 `foreign_indiv`・個人その他 `individuals_other`・計 `total`・単元未満株式の状況 `treasury_stock`）の単元数と割合を個別要素から読み、`ShareholdingByShareholderCategoryTextBlock` の HTML 表から株主数 `shareholders` と単元株数 `shares_unit`（`treasury_stock` を含む全区分）を補う。HTML 表で単元株数が読めないときは単元株数の個別要素や本文の `単元株数…N株` から補完する |

保有割合・所有割合は、XBRL の `decimals >= 2` かつ値が `1` 以下の比率表記（`0.165` など）であれば `100` 倍して百分率（`16.5`）に揃え、`decimals <= 0`（`-2` など既に百分率で格納されているもの）はそのまま保存する。

#### 訂正連鎖と最新フラグ

`refresh_versions` は `edinet_documents`（`120`〜`170`）の `parent_doc_id` をたどって大元の `original_doc_id` を埋め、取下げ・不開示を `withdrawn` に反映したうえで、`(code, fiscal_year_end, doc_type)` ごとに `withdrawn = False` の中で最も `(submitted_at, doc_id)` が新しい 1 件だけに `is_latest = True` を立てる。

実データの有報 4 社での検証結果は次のとおりである。

| 銘柄・書類番号 | 会計基準 | 抽出結果 |
|---|---|---|
| トヨタ自動車 `S100YWV5` | IFRS | 大株主 10 名（1 位 日本マスタートラスト信託銀行 1,874,970,000 株・14.37%）、所有者別 9 区分（単元株数 100 株・株主数まで一致） |
| キーエンス `S100Y50I` | 日本基準 | 大株主 10 名（1 位 ティ・ティ 33,261,896 株・13.71%）、所有者別 9 区分 |
| 日立製作所 `S100YGBO` | IFRS | 1 位 742,663,700 株・16.50%、金融機関 221 株主・27.80% |
| ソフトバンクグループ `S100YRK7` | IFRS | 1 位 孫正義 429,144,624 株・29.99%、個人その他 538,098 株主 |

### 大量保有報告書

対象ジョブは [`edinet/edinet_large_holdings`](gen/edinet.md#edinetedinet_large_holdings)（`jobs/edinet/edinet_large_holdings.py`）、表示画面は [銘柄詳細](../screen/stock.md) と [大量保有報告書](../screen/holders.md) である。

`edinet_documents` のうち `doc_type_code` が `350`（大量保有報告書・変更報告書）または `360`（訂正報告書）であり、`csv_flag = True`・`withdrawal_status != '2'`・`disclosure_status = '0'`・`doc_info_edit_status != '1'` の未処理書類を取得する。`type=5` の CSV ZIP（`XBRL_TO_CSV/jplvh*.csv`、UTF-16LE タブ区切り）をパースし、報告書ヘッダを `large_holding_reports`、提出者・共同保有者ごとの内訳を `large_holdings` に保存する。

#### 銘柄コード・数値変換・訂正管理

各列の変換と版管理の規則は次のとおりである。

| 項目 | 規則 |
|---|---|
| 発行体の銘柄コード `code` | 本文の `SecurityCodeOfIssuer`（4 桁または 5 桁）を `stocks` マスタと照合して 5 桁コードに揃える。空欄のときは `issuer_edinet_code` から `edinet_filers` 経由で引き、非上場銘柄などでマスタに無い場合は `raw_security_code` だけ残して `code = NULL` にする |
| 保有割合と増減 `ratio_delta` | 比率表記（`decimals >= 2` かつ絶対値 `<= 1`、または直近値との整合）を `%`（`0〜100`）に揃える。直前の保有割合 `prev_holding_ratio` が入っていれば `total_holding_ratio - prev_holding_ratio` を `ratio_delta` に入れる（新規報告などで直前値が無ければ `NULL`） |
| 訂正連鎖と `is_latest` | `360` の `parent_doc_id` を大元の `350` までたどって `original_doc_id` に格納し、同じ `original_doc_id` の中で取下げでなく `parse_status='parsed'` の最新 1 件だけを `is_latest = True` にする |
| 期間外・`404`・`5xx` | 縦覧期間（5 年）を過ぎた未処理書類や取得時 `404` は `parse_status='failed'`（`parse_error` に `OutsideEdinetWindow: ...` または HTTP 404 例外）で記録して次回から飛ばし、終了コードは `0` にする。`5xx` やタイムアウトは行を作らずに終了コード `1` を返して次回再試行する |

#### 報告義務発生日の誤記補正

提出者が年を取り違えて未来の日付（たとえば `2026-04-06` のつもりで `2027-04-06`）を `DateOfFilingRequirementArisen` に書いて提出し、訂正報告書も出さない事例がある（`KAB-372`）。

確認済みの誤記は `config/large_holding_date_fixes.json` に `doc_id` と正しい `obligation_date` を登録し、`python jobs/edinet/edinet_large_holdings.py --reparse-fixes` で既存の報告書を再パースして反映する。また通常実行の末尾でも毎回 DB 全体を検査し、補正台帳に未登録のまま `obligation_date` が提出日から `FUTURE_DAYS`（30 日）以上未来になっている報告書があれば `WARNING edinet lvh obligation-date-future` をログに出す。

#### 画面 API での引き方と絞り込み

`app/holders.py` が画面向けに引くときの規則は次のとおりである。

1. 銘柄詳細の大量保有タブ（`GET /api/stock/{code}/holders`）は、`code = ? AND is_latest = 1 AND withdrawn = 0 AND parse_status = 'parsed'` の報告書を取り出し、提出者（`filer_edinet_code`、無ければ `filer_name` → `doc_id`）ごとに `(file_date, submit_datetime, obligation_date)` が最大の最新報告書を並べる。
2. 保有者ページ（`GET /api/holders/{edinet}`）は、`large_holdings.edinet_code = ?` で有効な最新報告書を引き、発行体ごとに最新の保有状況と履歴を束ねる。
3. 横断の大量保有報告書一覧（`GET /api/holders`）は、上場銘柄（`code IS NOT NULL`）に絞ったうえで、保有割合が変わらない変更報告書（`ratio_delta == 0`）と、保有目的が純投資のみ（`純投資`・`投資一任契約`・`投資信託` を含み、`重要提案`・`経営参加`・`政策保有`・`業務提携`・`支配権`・`公開買付`・`役員派遣` を含まない）で共同保有者の内訳組み替えだけを行った報告書を既定で除外する。

### 自己株券買付状況報告書

対象ジョブは [`edinet/edinet_buybacks`](gen/edinet.md#edinetedinet_buybacks)（`jobs/edinet/edinet_buybacks.py`）、表示画面は [銘柄詳細](../screen/stock.md) である。

EDINET での縦覧期間が 1 年（`KEEP_YEARS = 1`）と短いため、日次 cron で取りこぼさずに保存する。`edinet_documents` のうち `doc_type_code` が `220`・`230` で、`csv_flag = True`・`code IS NOT NULL`・`withdrawal_status != '2'`・`disclosure_status = '0'`・`doc_info_edit_status != '1'` の書類を対象とし、報告書ごとの月次サマリを `edinet_buybacks`、決議ごとの枠と進捗を `edinet_buyback_resolutions` に保存する。

#### 2 段構えの取得経路

自己株券買付状況報告書の本文は個別数値タグではなく HTML 表入りの `TextBlock` として格納されるため、次の 2 経路で読み取る。

| 優先順 | 見るファイルと要素 | 使う場面 |
|---|---|---|
| 1. `type=5` の CSV | `XBRL_TO_CSV/jpcrp060000*.csv` の 4 つの `TextBlock`（株主総会決議 `AcquisitionsByResolutionOfShareholdersMeetingTextBlock`・取締役会決議 `AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock`・処分 `DisposalOfTreasurySharesTextBlock`・保有状況 `HoldingOfTreasurySharesTextBlock`） | 標準の提出書類 |
| 2. `type=1` の iXBRL 本文 | `XBRL/PublicDoc/*_ixbrl.htm`（ファイル名に `honbun` を含むもの優先）の `１【取得状況】` 配下の生 HTML 表（`parse_acquisitions_honbun`） | 提出者が取得状況の表に XBRL タグを付け忘れ、`type=5` に総会・取締役会の両 `TextBlock` が欠落している書類（`S100Z1HS`・`S100YUHP` など、`KAB-447`） |

#### 実データの表揺れとパース規則

各社の HTML 表のレイアウト揺れ・漢数字単位・端数表記に対して、推測や勝手な単位換算を行わずに次の規則で数値を確定する。

| 項目・ケース | パース規則 |
|---|---|
| 転置レイアウト・種類株式列・報告月併記 | 行と列が転置した表（メルカリ `S100Z824`）、普通株式と優先株式が列で分かれた表（りそな HD `S100Z5JO`）、決議日セルに `（報告月）` が併記された表（堺化学工業 `S100Z5K9`・`S100Z5L3`）をそれぞれ構造判定して同じ列に正規化する |
| 漢数字の単位と脚注番号 | `50億`（`5,000,000,000`）や `1,800百万`（`1,800,000,000`）などの単位表現を展開し、小数点以下は整数へ切り捨てる。セル末尾の脚注番号（`※1`・`*2`・`(注)1`・`注1`）は数値と連結して誤読しないよう先に除去し、`100.00(100.00)` のような併記は先頭の数値を読む |
| 日別明細が無く `報告月` 行が 1 行だけの表 | `計` 行が無くてもその単一行を報告月の取得実績として読む（`S100YBMN`、`KAB-463`） |
| `計` 行を省略した 0 株の日別行 | 日別行がすべて `0` 株（空欄や `-` を除く）のときだけ `0` 株として扱い、正の株数の日別行があるのに `計` 行が無い表は日別合算で推測せずにエラーにする（`S100Y8WI`・`S100XZ69`、`KAB-475`） |
| 約定ベースと受渡ベースのズレ | 累計行が `0` なのに報告月が正の株数になっている表は、同じ表または欄外に `約定ベース` と `受渡ベース` の両方への言及があるときだけ月末約定・翌月受渡しの期ズレとして通す（`3151` バイタルケーエスケー HD `S100Z275`・`S100Z80G`・`S100ZHJ0`、`KAB-488`） |
| 報告月見出し行の `100` 誤記と `計 -` | `（報告月における取得自己株式）` 行に `100`・`-`・`-`、直後の `計` 行に `-`・`-`・`0.00` が並ぶ表は、株数が累計以下であれば進捗上限 `100%` の誤記とみて報告月を `0` にする（`S100YWV5`、`KAB-488`） |

次の不整合がある書類は推測で埋めず `parse_status='failed'`（`parse_error` に理由を記録）にする。

1. 株数・金額セルに `%` が混入している、上記の期ズレ注記なしに `報告月 > 累計` になっている、または報告月の比率が `報告月 ÷ 決議枠` と `0.5pt` を超えて食い違う。
2. タグ無し HTML 本文（`parse_acquisitions_honbun`）で、株主総会・取締役会・役員会の見出しが無い、単位が `株`・`円` 以外（`千株`・`千円` など）になっている、`累計 > 決議枠`、進捗率が `累計 ÷ 決議枠` と `0.5pt` を超えてずれる、同じ `(kind, resolution_date)` が重複する、`１【取得状況】` を持つ本文ファイルが複数ある、または決議が 0 件なのに「該当事項はありません」の記載も無い。

#### 訂正管理と画面での利用

`refresh_versions` は `parent_doc_id` をたどって `original_doc_id` を埋め、`doc_metas` で修正通知後の最新メタデータを反映したうえで、`original_doc_id` ごとに有効な最新 1 件へ `is_latest = True` を付ける。複数決議が並走している報告月では、株主総会決議と取締役会決議が同じ枠の授権と実行であることも別枠であることもあるため、`edinet_buybacks` の月次・累計は決議枠の合算ではなく、その月に動いた行の合計（累計は最大値）を入れ、決議ごとの正確な内訳を `edinet_buyback_resolutions` に持つ。

銘柄詳細の株主タブ（`GET /api/stock/{code}/holders` の `treasury`）は、`is_latest = True` かつ `withdrawn = False` の最新の報告月（`report_month DESC, file_date DESC`）から自己株式比率（`treasury_shares / issued_shares`）と取得期間中（`acquire_to >= today`）の決議枠・進捗率を返し、月次の取得推移も並べる。

### 公開買付け

対象ジョブは [`edinet/edinet_tender_offers`](gen/edinet.md#edinetedinet_tender_offers)（`jobs/edinet/edinet_tender_offers.py`）、表示画面は [銘柄詳細](../screen/stock.md) である。

公開買付けは買付者（届出書・撤回・報告書）と対象会社（意見表明・対質問回答）の双方が別々の書類として提出するため、書類ごとの明細を `edinet_tender_offer_docs` に入れ、`refresh_offers` で同じ案件の書類群を 1 行の `edinet_tender_offers` に束ねる。

#### 書類種別ごとの読み取り方法

`doc_type_code` が `240`〜`340` で `withdrawal_status != '2'`・`disclosure_status = '0'`・`doc_info_edit_status != '1'` の書類を次の方法で処理する。

| `doc_type_code` | 取得ファイル | 抽出する主な項目 |
|---|---|---|
| `240`・`250`（公開買付届出書・訂正） | `type=5` CSV（`jptoo-ton*`） | 対象会社の EDINET コードと社名・買付者名・普通株 1 株あたり買付価格 `price_per_share`（円）と原文 `price_text`・買付期間・買付予定数の下限 `min_shares` と上限 `max_shares`・目的・買付後所有割合 |
| `270`・`280`（公開買付報告書・訂正） | `type=5` CSV（`jptoo-tor*`） | 買付期間・応募株数 `applied_shares`・買付株数 `purchased_shares`・成否 `outcome`（`succeeded` または `failed`）・買付後所有割合 |
| `290`・`300`（意見表明報告書・訂正） | `type=1` ZIP 内の `PublicDoc/*.htm` | XBRL・CSV が存在しない（`csv_flag = False`）ため HTML 本文を読み、提出者自身を対象会社としたうえで `公開買付者の名称` と意見 `opinion`（`賛同`・`反対`・`中立`・`留保`）を抽出する |
| `260`・`310`〜`340`（撤回・対質問回答・特例・訂正） | 取得しない（一覧メタデータのみ） | `parse_status='meta'` で記録する。`260`（公開買付撤回届出書）が出た案件はステータスを `withdrawn` にする |

縦覧期間（5 年）より前の未処理書類は、本文を読む種別（`240`・`250`・`270`・`280`・`290`・`300`）なら `parse_status='failed'`（`OutsideEdinetWindow`）にし、もともと一覧メタデータしか使わない種別（`260`・`310`〜`340`）なら `parse_status='meta'` で記録する。

#### 案件の名寄せと集約規則

EDINET では訂正書類や撤回届出書（`260`）には `parent_doc_id` が入る一方、公開買付報告書（`270`）や対象会社が出す意見表明報告書（`290`）には `240` への `parent_doc_id` が入らない。そこで `refresh_offers` は次の順で同一案件キー `offer_key` を決める。

1. `parent_doc_id` の連鎖をたどり、根が `240` に行き着く書類（`250`・`260` など）はその `240` の `doc_id` に束ねる。
2. `270`（およびその訂正 `280`）は、対象会社（`target_code` または `target_edinet_code`）と正規化した買付者名が一致する `240` のうち、`period_start` が一致するか `240` の提出日から 180 日以内に出たものに束ねる。
3. 対象会社側の書類（`290`・`300`・`310`・`320`）は、同じ対象会社の案件のうち `[最初の提出日 - 5日, max(period_end, 最終提出日) + 14日]` の窓に `file_date` が収まる案件へ束ねる（複数案件が重なるときは買付者名の一致で絞る）。
4. 取り込み開始より前に出た案件の続報など、対応する `240` が無い書類は `doc:{root_doc_id}` の単独案件として残す。

集約行 `edinet_tender_offers` では、買付価格や下限・上限株数を最新の `240`・`250` から採り、`period_start` は `240`・`250` 優先（無ければ `270`・`280`）、延長が反映される `period_end` と最終の `holding_ratio_after` は `270`・`280` 優先（無ければ `240`・`250`）で埋める。案件ステータス `status` は届出時点で `open`、報告書が出たら `succeeded` または `failed`、撤回届出書が出たら `withdrawn` になる。

実データの検証例として、トヨタ不動産による豊田自動織機（`62010`、`S100Y3N0`）の公開買付けで訂正による買付価格の引き上げ（16,300 円 → 18,800 円 → 20,600 円）と期間延長が追えること、および牧野フライス製作所（`61350`）に対するニデックの敵対的 TOB（撤回 `S100YZ5A`）と MBK パートナーズのホワイトナイト TOB（賛同 `S100YU44`）が別案件として正しく分離されることを確認している。

---

## 運用

### cron とログ

すべての定期実行スケジュールは `deploy/crontab` で管理し、詳細は [ジョブ設計](gen/edinet.md) と [バッチ一覧](../JOBS.md) に出力される。各ジョブは `deploy/cron-run.sh` 経由で `/var/log/stock-analyzer/<job>.log` に出力され、MCP の `batch_logs` で確認できる。また MCP の `db_stats` では `edinet_documents`・`edinet_list_days` の件数・日付範囲・紐付け数・確定日数と、各 EDINET テーブルの件数を確認できる。

監視（`deploy/alert_patterns.py`）は `ERROR`・`Traceback`・非ゼロ終了（`[ALERT] ... exited with code`）で Slack 通知を飛ばす。そのため、再試行しても直らない恒久的な欠損（縦覧期間切れや `404`）は DB に `failed` を残して終了コード `0` で終え、一時的な通信障害（`5xx`・タイムアウト）や財務 XBRL のパース失敗だけを終了コード `1` にして通知させる。

### EDINET のメンテ

金融庁は日曜深夜や四半期末のメンテナンス時間帯に EDINET API を停止し、HTTP `503`（JSON 本文の `"status": "503"` または HTML のメンテナンス画面）を返す。

`app/edinet/client.py` の `run_edinet_job` は `EdinetMaintenanceError` を捕捉すると、エラー通知を出さずに `WARN edinet maintenance skip job=<job> err=<exc>` を 1 行だけ出力して終了コード `0` で抜ける。翌日以降の通常実行で `edinet_documents` が直近 8 日分と未確定日を自動で取り直すため、人手での穴埋めは要らない。

### ZIP の退避と過去分の取り直し

有報・大量保有・自己株・公開買付けの生 ZIP を数年分すべて VPS のローカルディスクに置き続けると容量を圧迫するため、日次の運用ジョブ `jobs/ops/backup_data.py`（[SQLite バックアップ](../DESIGN_backup.md) 参照）で OneDrive への退避とローカル削除を行う（`KAB-439`）。

1. `rclone copy` で `data/edinet` から `{BACKUP_OFFSITE_REMOTE}:{BACKUP_OFFSITE_PATH}/data/edinet`（既定は `onedrive:stock-analyzer-backups/data/edinet`）へ未転送の ZIP を追記コピーする（`sync` ではないため、ローカルから消したファイルも OneDrive 側には恒久的に残る）。
2. コピーが成功したあと、`edinet_archives` で `status='saved'` かつ保存日時 `saved_at` が `EDINET_LOCAL_KEEP_DAYS`（既定 `30` 日）より古いローカル ZIP をディスクから削除し、空ディレクトリを掃除する。`edinet_archives.status` は `'saved'` のまま維持する。
3. 後日パーサーを改修して `--reparse`・`--doc-id`・`--retry-failed` などで過去書類を読み直すときは、`fetch_zip` が OneDrive から自動でローカルへ書き戻すため、EDINET の縦覧期間（自己株 1 年・その他 5 年）が切れた後でも再パースできる。大量の ZIP を一括で手元に戻したいときは `app/edinet/archive.py` の `rclone_copy`（または `rclone copy` コマンド）で対象種別ディレクトリごと復元してからジョブを流す。

### 初回投入の手順

空の環境に EDINET データを最初に入れるときは、銘柄マスタ（`run_batch.py`）が入った状態で次の順に実行する。

```bash
python jobs/edinet/edinet_codes.py
python jobs/edinet/edinet_documents.py --backfill-years 5
```

一覧が入ったあとは日次 cron が `--limit` 件ずつ順番に消化する。初回にまとめて全件パースしたいときは、各ジョブ（`edinet_xbrl.py`・`edinet_large_holdings.py`・`edinet_buybacks.py`・`edinet_tender_offers.py`）に `--limit 0` を付けて手で実行する。
