# 取込と保存の規則: 株価・決算

ジョブの起動時刻・引数・読み書きする表は [ジョブ設計](gen/market.md)（自動生成）。ここには規則だけ書く。各節の頭の「ジョブ」は、生成ページの同じ名前の節へのリンク。

## 日次バッチ

ジョブ: [market/run_batch](gen/market.md#marketrun_batch)（`jobs/market/run_batch.py`）

決算開示は15時以降が多いため、当日夜(19:30)に1回実行する。
19:30 より後の開示（全体の 0.3% 程度）は、翌営業日の回が前営業日の決算を取り直して拾う。

### 手順

1. `/equities/master` で銘柄マスタ更新 (ProdCat 含む)
2. `/equities/bars/daily?date=当日` + `/indices/bars/daily/topix` 取込。戻り値は全部保存する。
   AdjFactor ≠ 1（権利落ち日）の銘柄は、その銘柄の調整後の値を DB の中で計算し直す（`app/market/adjust.py`）。
   権利落ちで過去の Adj* がさかのぼって直るので、差分だけでは DB の過去分が古いまま残る。
   API で取り直すとプランの期間より前が取れない。
   そこで、後から変わらない調整前の値と AdjFactor から J-Quants と同じ手順で出す
3. `/fins/summary?date=当日` と `/fins/summary?date=前営業日` 取込。前営業日は DB の株価で当日より前の最終日
4. indicators 再計算 (MA/52週/RS/ステージ。RSユニバースは内国株券のみ) → earnings_events 更新。
   飛ばす条件は下の表。
   指標は全件計算し、今の行と突き合わせて変わった行の upsert と消えた行の削除だけ書く（全件の削除と入れ直しは本番規模で数分かかる）。
   書き込みはチャンクごとに commit する（1 本のライターロックを `busy_timeout` 以上握らない）
5. `/fins/earnings-date?date=当日` と `date=昨日`。昨日は暦日。4 の後。株価が 0 件でも行う。失敗しても 4 の結果は残し、プロセスは非ゼロで終わる

| 条件 | 手順 4 |
|---|---|
| 2〜3 が 0 件（前営業日は新しく増えた件数）で、indicators が株価の最終日まで済んでいる | 飛ばす |
| それ以外 | 行う |

### リクエスト数

定常の夜は **計7リクエスト**。内訳はマスタ、株価、TOPIX、財務の当日、財務の前営業日、発表予定の当日、発表予定の昨日。発表予定の2本は `/fins/` の枠。

台帳が空の初回は指標のあと PubDate を `EARNINGS_DATE_LOOKBACK_DAYS` 日さかのぼる。この歩きは 7 に入らない。

### 期の日付の誤り（`statement_period_fixes.json`）

J-Quants の応答には、当期の日付（`CurPerSt` / `CurPerEn` / `CurFYSt` / `CurFYEn`）が 1 年前になっている開示がある（KAB-454。API を引き直しても同じ）。
規則では直さない。短信の原本などで確かめた開示だけを `config/market/statement_period_fixes.json`（開示番号・J-Quants が返す誤った期末 `period_end`・根拠）に載せる。

`statements` の行を作る `_statement_row` が次のように扱う。日付ごと・銘柄ごとの取込とバックフィルはどれも `_statement_row` を通る。

| 条件 | 扱い |
|---|---|
| 開示番号が一覧にあり、応答の `CurPerEn` が一覧の期末と同じ | `period_start` / `period_end` / `fiscal_year_start` / `fiscal_year_end` を 1 年進める（月末は翌年の同じ月の月末） |
| 開示番号が一覧にあり、期末が一覧と違う | 直さず `WARNING jquants statements period-fix-mismatch` を出す（J-Quants が直したら一覧から外す） |
| 開示番号が一覧に無い | 直さない |

- `raw_json` は応答のまま
- 短信 XBRL から作った行（`source='xbrl'`）の日付は J-Quants の取込で上書きしない（`docs/jobs/xbrl.md` §3.2）。その行は一覧に載せても日付が変わらない

### 期の誤りの候補（`period-candidate`）

日次の回は、J-Quants から初めて入った開示（XBRL から作っただけの行を含む）のうち、次の表に当たるものを `WARNING jquants statements period-candidate` で出す。直さない。一覧の開示は除く。

| 開示 | 候補にする条件 |
|---|---|
| 通期 | 提出日が期末から 300 日以上あと、かつ `NxtFYSt` が `CurFYEn` の翌日でない |
| 四半期（1Q〜3Q の短信） | 提出日が期末から 300 日以上あと、かつ同じ銘柄・同じ期（`type_of_period`・`period_end`）の前の短信と売上高が違う |

- 四半期の条件は 1 年後の出し直し（訂正）も拾うので候補にとどまる
- 1 つの開示で出るのは J-Quants から初めて入った回だけ。なので確認済みの一覧は持たない（バックフィルと前営業日の取り直しでは同じ開示を出し直さない）
- 候補は原本の期の印字で確かめる。誤りなら一覧に足して、既存の行を `tools/market/fix_statement_periods.py` で直す（手順は `deploy/README.md`）

### 再実行・バックフィル・取り直し

冪等設計 (UPSERT) のため何度実行しても安全。

| 実行 | 対象 | 中断・失敗のとき | 非ゼロ終了 |
|---|---|---|---|
| 日次 | 当日と前営業日 | — | 発表予定（手順 5）の失敗 |
| バックフィル | 株価と決算を同じ日に commit できた日付をスキップ | 再実行すれば続きから埋まる | カレンダーが空、またはその日が失敗 |
| `run_batch.py --refetch-prices` | UL / LL が NULL の行が残る日だけ | 中断しても再実行で続きから。失敗した日は飛ばす | 失敗した日があれば最後に |
| `run_batch.py --readjust-prices` | 全銘柄 | — | — |

表の「非ゼロ終了」は規則として決めた条件。ほかに API の失敗などの例外でも非ゼロで終わる。

- `--refetch-prices` は DB にある日足を日付ごとに全部取り直し、指標を計算し直す。
  1 日 1 リクエストで、5 年分なら約 1,250 日 ≒ 20〜40 分。
  プランの期間より前の日は 400 で失敗のまま残る（その日の調整後の値は `--readjust-prices` で直る）
- `--readjust-prices` は全銘柄の調整後の値を DB の中で計算し直し、指標を計算し直す。API は呼ばない

## 決算の数字の扱い

### Q単独値の算出 (保存せずAPI側で導出)

`Q単独 = 当期累計 − 前四半期累計`(1Qはそのまま)。前年同期のQ単独と比較しYoY成長率を計算。EPSも同様。

| `CurPerType` | Q単独 |
|---|---|
| `1Q` | 当期累計そのまま |
| `2Q` / `3Q` | 当期累計 − 前四半期累計 |
| `4Q` / `FY` | 通期累計として同一視し、当期累計 − 前四半期累計 |
| `5Q` | 算出しない。決算期変更等の変則期で、前後関係が定義できないため |

`CurPerType` が取る値は `1Q/2Q/3Q/4Q/5Q/FY`。

QoQ は `Q単独(当四半期) vs Q単独(直前四半期)`。1Qの相手は前事業年度の4Q単独になるため
季節性が断絶する(UI側で注記する)。算出しないと5月の決算集中日にQoQ列が全社空欄になる。

### 速報一覧に出す開示 ★注意

| 開示 | 一覧 |
|---|---|
| 予想修正のみ(`EarnForecastRevision` 等) | 除外する。実績値が空 |
| 既出期間の再提出で、**売上・営利・経利・純利が初回と一致するもの** | 除外する |
| 既出期間の再提出で、損益が変わったもの | 「訂正」として残す |
| 複数期を同時に「初めて」開示する日 | 期ごとに残す。1銘柄1行には潰さない |

遅延企業は過去の期をまとめて再提出することがあり、そのまま出すと1銘柄で何行も並ぶ。

**EPSは比較に含めない。**
分割・増資で遡及修正されるだけのことがあり、訂正扱いにすると
損益が変わっていない再提出まで一覧に残ってしまう。

カレンダーの件数も同じルールで数え、一覧の行数と一致させる。

### 前年同期の突合

事業年度末の完全一致ではなく **±45日の最近傍一致** で引く(`config.EARNINGS_PARAMS`)。
決算期変更で年度末がずれる銘柄と、2/29決算(`date.replace(year=…)` が ValueError になる)を同時に吸収する。

### 増減率の表現 (黒字転換)

前期が0以下だと比率に意味がないため、増減率は `{pct, state}` で返す。
`state` は `up`/`down`/`turn_profit`(黒転)/`turn_loss`(赤転)/`loss`(赤字継続)/`na`。
単純に `None` にすると赤字からの黒字転換が「–」に潰れる(実データで営業赤字は約4,350件)。
売上はマイナスを取らないので黒転の概念を適用しない。

### 予想修正の検知

同一事業年度の、発表日が前の開示の通期営業利益予想 (`statements.fy_operating_profit`) と比較する。
閾値は ±0.1% (`EARNINGS_PARAMS["revision_noise_th"]`)。ホバーに修正率。

| 条件 | 一覧の時刻列 |
|---|---|
| 直前比が +0.1%以上 | `上方` |
| 直前比が −0.1%以上の減少 | `下方` |
| 直前が 0・欠損、当期が欠損、閾値未満 | マークなし |
| 既出期間の損益訂正 (`restated`) | 向きを付けず `訂正` のまま |
| 予想修正と損益訂正の両方 | 両方出す |

- 判定は営業利益だけ。売上・経常・純利益が逆向きでも営利に従う
- 赤字予想は `|直前|` を分母にし、損失縮小を上方にする
- 同日の予想修正のみの開示 (`is_revision`) は直前に使わない（短信と `EarnForecastRevision` が同日だと新しい値同士で消えるため）

向き（上方/下方）そのものを保存した列や表は無い。あるのは次で、時間列のマークには使えない。

| 保存先 | 中身 | 使えない理由 |
|---|---|---|
| `statements.is_revision` | 予想修正**だけの**開示か（`EarnForecastRevision` 等） | 向きが無い。一覧から除外している（実績が空） |
| `statements.type_of_document` | 短信 / 予想修正 / 配当修正 | 種別だけ。`上方` / `下方` の語は無い |
| `statements.fy_*` | その開示の通期会社予想（金額） | 向きでは無い。直前比は API で出す（上の判定） |
| `xbrl_filings.doc_type` | 題名から `forecast_revision` 等 | 向きが無い。決算速報の行は `statements`。ゴールデン DB は 0 件 |
| `xbrl_financials.value_kind=forecast_previous` | 修正 XBRL の前回予想（PreviousMember） | 前回と今回の金額はあるが、向き列は無い。一覧と紐づかない。ゴールデンは 0 件。短信に埋め込まれた修正（一覧の大半）は PreviousMember が無いことが多い。修正履歴も前の開示から組む（`forecast_previous` は使わない） |
| `raw_json` の `ChgByASRev` 等 | 会計方針・見積りの変更フラグ | 業績予想の上方/下方ではない |
| `earnings_events.is_first` | その期の最初の短信か | 向きが無い。訂正・予想修正だけの行を落とすフラグ |
| `quarter_summary.revised_up` | 上記判定の派生（計算値） | テーブルではない。一覧 API はこれまで出していなかった |

### 会計基準による欠損 ★注意

IFRS・米国基準の開示には**経常利益の概念が無く `OdP` は空欄**になる。
経常利益でのスクリーニングやソートは欠損前提で扱うこと(営業利益を主指標にする)。

## 決算説明資料

保存先・表題の採否・索引は 3 つの取得経路（TDnet・IR BANK・会社の IR ページ）で共通。

### 表題の採否（`is_material_title`）

採否は `app/market/briefing_title.py` の `is_material_title` だけ（保存も、あとから同じ判定が要る処理もここを呼ぶ）。判定は空白と全角半角を畳んだ表題で行う。

| 表題 | 扱い |
|---|---|
| 決算・四半期・中間・通期・業績のどれかと、説明資料・説明会資料・補足資料・プレゼン資料（プレゼンテーション資料を含む）・質疑応答（主な質疑、Q&A。KAB-336）のどれかを両方含む | 採る |
| 質疑応答の語が要旨・概要だけ | 採らない（KAB-336） |
| 英語: earnings / financial results の presentation、または briefing / supplementary materials、または Q&A / questions and answers | 採る |
| 決算短信の補足資料 | 採る |
| 訂正・誤記 | 落とす |
| お知らせ、開催・中止・延期、掲載について・公開について（決算説明会開催のお知らせ、説明資料を載せた案内） | 落とす |
| 英語の notice / correction | 落とす |
| 資料の語も質疑応答の語も無い「決算説明会」 | 落とす |
| 決算短信そのもの | 落とす |
| 成長可能性の説明資料、決算などの語が無い補足説明資料 | 落とす |
| 中期経営計画の説明会資料（決算・四半期などの語が無いもの） | 落とす |
| 「修正」だけ | 落とさない。補足資料の表題に業績予想の修正が入ることがある |

### 保存先と索引

- 保存先は `data/briefing/{code}/YYYYMMDD_CODE_名前_表題.pdf`。`{code}` とファイル名は月次 PDF と同じ（display_code。J-Quants の 5 桁にはしない）。`SHARE_ROOT` を置くとその直下の `briefing/`
- 索引は `briefing_documents`。列の一覧は [データモデル](../schema/market.md)。storage は `local` / `onedrive`（既存行は `local`。KAB-345）。rel_path は `data/briefing` からのパス。code は 5 桁
- `storage=onedrive` の行は手元が無くても取り直さない

| source | 書くジョブ | document_id | status / storage |
|---|---|---|---|
| `tdnet` | TDnet のジョブ（下） | TDnet の PDF ファイル名 | `saved` / `local` |
| `irbank` | IR BANK の過去分（下） | 18 桁の開示番号 | — |
| `homepage` | 会社の IR ページ（下）。TDnet のジョブでは取得しない。記録は `app/market/briefing_store.record_briefing` | `hp` + sha1 の頭 24 桁（下） | storage=`local` |

### TDnet の決算説明資料（`tdnet_briefing_r2.py`）

ジョブ: [market/tdnet_briefing_r2](gen/market.md#markettdnet_briefing_r2)（`jobs/market/tdnet_briefing_r2.py`。KAB-327）

- 一覧は `jobs/_tdnet.py` の `fetch_days`。採否は上の `is_material_title`
- 日次の `backup_data` には載せない（コピーだと手元が残る）。1 年以上前の PDF は下の `briefing_offsite` が移す。TDnet は約 1 か月で PDF を消す

| 既存ファイル | 扱い |
|---|---|
| PDF | 取り直さない。索引が無ければ索引だけ足す。その件数は `reindexed`（新規に保存した `saved` とは別） |
| PDF でない | 取り直す |

| 回 | 対象の日数 | 使い分け |
|---|---|---|
| 毎時の短信の取り込みのあと（`tdnet_briefing_r2.py 1`。KAB-438） | 当日分 | 当日の資料をすぐ取る |
| 日次 cron | 直近 7 日 | 取りこぼしの拾い直し |
| 手で一度（`deploy/cron-run.sh jobs/market/tdnet_briefing_r2.py 30`） | 約 30 日 | 初回。デプロイでは走らない |

### IR BANK の過去分（`irbank_ingest.py`）

ジョブ: `tools/briefing/irbank_ingest.py`（KAB-330）。`tools/` にあるので生成ページには無い。

- TDnet が消した分を一度だけ手で取る。cron には入れない。毎日の分は上の `tdnet_briefing_r2.py`
- 手での実行は `deploy/README.md`。デプロイでも cron でも走らない

対象の銘柄（KAB-325 の `tanshin_covers` と同じ考え方）:

| `stocks.product_category` | 扱い |
|---|---|
| 内国株券 `011`・外国株券 `021`（普通株） | 対象 |
| REIT（`013`） | 対象。ただし名称に「ファンド」または「インフラ」がある投資法人は除く |
| ETF（`014`）・外国 ETF / ETN（`023`） | 除く |
| 区分が空 | 名称がファンド・投資信託・ETF・ETN でなければ残す |

一覧と取得:

- 一覧は `https://irbank.net/{4桁}/ir` を 1 社 1 回。同じ 4 桁の株は末尾 0 の行を代表にする。表題の採否は `is_material_title` だけ
- PDF は `https://api.irbank.net/api/market-files/v1/tdnet/{18桁の末尾14桁}.pdf`（`tools/tdnet_pdf/ingest.py` と同じ）
- 保存先とファイル名は TDnet のジョブと同じ（`data/briefing/{4桁}/YYYYMMDD_CODE_名前_表題.pdf`）。索引の code は 5 桁、document_id は 18 桁の開示番号
- 同じ番号、またはその末尾 14 桁が、どの source でも索引にあれば取り直さない。成功した PDF は索引にあるので取り直さない

進捗（`briefing_irbank_scans`。列は code / scanned_at / listed / saved / skipped / reindexed / missing / failed）:

| 銘柄の結果 | `briefing_irbank_scans` の行 |
|---|---|
| 読み終えた | 書く |
| PDF が 404 だけ | 書く |
| 429 / 503 で止めた | 書かない（次の実行が続きから） |
| 空き不足で止めた | 書かない（次の実行が続きから） |
| PDF の取得に失敗した | 書かない（次の実行が続きから） |

空きと古い資料（KAB-355）:

- PDF を書く直前に `shutil.disk_usage(--dest).free` を見る。25GiB（`MIN_FREE_BYTES`）未満ならその PDF は書かず `stopped=disk_low`、終了コード 0。一時ファイルの直前も同じ
- 同じ `code` の既存索引と今回の一覧を合わせた最新から 1 年以上前の PDF は `data/briefing` に置かない。一時ファイルからその会社の分だけ下のバッチで OneDrive へ送り、一致したものだけ索引を最初から `storage=onedrive` で書く。最新そのものは手元に残す

DB の書き込み（KAB-305 と同じ。KAB-332）:

- 一覧の SELECT は読み終わったら閉じ、PDF の取得中はトランザクションを持たない
- 索引（`briefing_documents`）と進捗（`briefing_irbank_scans`）の UPSERT は `run_sqlite_write` で locked を数回、待ちを伸ばして再試行する

引数の使い分け:

| 引数 | 使いどころ |
|---|---|
| `--rescan` | `briefing_irbank_scans` を無視して一覧を読み直す（`--code` と `--all` のどちらとも。KAB-338）。索引にある PDF は取り直さず、新しく対象になったものだけ保存する。付けないときの対象は変わらない。`--rescan` でも `remaining` は scans が無い社数のまま（読み直した社は残件に入らない） |
| `--limit` | この実行の社数 |
| `--sleep` | 請求の間隔（既定 1 秒） |
| `--dry-run` | 一覧だけ見る。PDF も索引も `briefing_irbank_scans` も書かない（見た社は残件のまま） |

### 会社の IR ページ（`homepage_briefing.py`）

ジョブ: [market/homepage_briefing](gen/market.md#markethomepage_briefing)（`jobs/market/homepage_briefing.py`。KAB-413）

TDnet に説明資料を出さない会社の分（例: アルバック）。手での実行は `deploy/README.md`。

#### 設定

- 対象は `config/market/briefing_pages.json` の `issuers`（code / name / index_url、任意で link_re）
- index_url は資料が並ぶ一覧ページ。過去分が別のページにある会社は配列で複数書く（同じ資料が両方にあれば 1 本）
- 転送や meta refresh で年度のページへ飛ばす会社は、飛ぶ前の URL を書く（ジョブが 2 回までたどる）
- TDnet に資料を出す会社は載せない（開示番号が無いので TDnet の行と 1 つにまとまらず、同じ資料が 2 行になる）

#### 会社のページのリンクの採否

一覧ページの `<a href>` のうち、パスが `.pdf` で終わるものを、リンクの文字で決める。一覧は決算の資料置き場なので、決算の語は補って判定する。資料の種類（説明資料・質疑応答など）はリンクの文字に要る。

| リンク | 扱い |
|---|---|
| リンクの文字が `is_material_title` を通る | 採る |
| リンクの文字に種類の語が無いが、`link_re`（リンクの文字に当てる正規表現）に合う（例: 日立の「プレゼンテーション:…連結決算の概要」） | 採る |
| 決算短信のリンク | 採らない |
| 文字の無いアイコンのリンク | 採らない |
| 表題に英文版の語（英文・英語・English。`is_english_version`）がある（KAB-434） | 取らない。日本語版と中身が同じなので |

リンクの文字の整え方:

- 容量や形式の注記（`(1.7MB)`、`[PDF: 548KB]`、`(PDF形式)`、末尾の `PDF`、「新しいウィンドウで開きます」）を落としてから判定と表題に使う。容量の表記が変わっても同じ資料のまま
- リンクの文字が「PDF」だけのときは、直前のリンクか見出しのあとに出た文字（隣の欄の資料名。60 字まで）をリンクの文字として使う（例: リクルート）
- リンクの中の `aria-hidden="true"` の要素と、`svg` / `style` / `script` の中の文字は読まない（例: 武田薬品は読み上げ用の文字とアイコンの題で同じ語が 3 回出る）

#### 表題・document_id・pubdate

- 表題は「h2 以下の見出しを上から並べたもの + リンクの文字」（例: `2027年6月期 第1四半期 決算説明資料`）。h1（ページ名）は入れない
- document_id は `hp` + sha1(code 5 桁・表題・PDF のファイル名) の頭 24 桁。URL の途中の番号は資料が足されるとずれるので使わない。会社がファイル名か見出しを変えると別の資料として取り直す

pubdate は一覧に日付が無いので、次の順で最初に取れたものを使う。

| 順 | pubdate |
|---|---|
| 1 | PDF の更新日時（JST に直す） |
| 2 | PDF の作成日時（JST に直す） |
| 3 | URL のパスにある YYYYMMDD |
| 4 | 取得した時刻。1〜3 がどれも無いとき、または 1〜3 で選んだ日時が未来のとき |

作成日時はひな形の日付のままの会社があるので更新日時を先に見る。会社が PDF を作り直すと開示日より後ろへずれる。

#### eir-parts のフィード

一覧を eir-parts（プロネクサスの IR サービス。`ssl4.eir-parts.net`）で出す会社は、会社のページに PDF のリンクが無い。`index_url` に eir-parts のフィード `https://ssl4.eir-parts.net/V4Public/EIR/<4桁>/ja/announcement/announcement_<N>.js` を書くと、HTML でなくフィードとして読む。

- フィードは JSONP。N は会社ごとで 1〜10 に散らばる。同じ中身のフィードを複数持つ会社が多いので、決算資料のいちばん多い 1 本を書く（KAB-430）
- 会社のページと違って決算以外の IR 資料（事業説明会、個人投資家向け説明会など）も並ぶ。なので決算の語は補わず、表題そのものが `is_material_title` を通るものだけ採る
- pubdate はフィードの `date`（時刻が入る行と 00:00:00 の行がある）。document_id は `hp` + sha1(code 5 桁・PDF の URL のパス) の頭 24 桁
- 表題に年度が無ければ `fiscal_ym` から「2027年3月期」を頭に付ける

| 項目 | 扱い |
|---|---|
| `type` が `pdf` で `news_type` が `ir_material` で始まり、表題が `is_material_title` を通る | 採る |
| `link_re` に合う（例: 丸紅の「IR資料」「質疑応答」） | 採る |
| 短信・適時開示（`tanshin` / `press`） | 採らない |
| 同じ決算期・四半期（`fiscal_ym` / `quarter`）に日本語の表題の資料があり、表題に日本語が無い | 英文版として採らない（日本語版を出さず英語の表題だけの会社の分は残る） |
| 同じ表題・同じ日の資料が複数の分類に載っている | 最初の 1 本（中身が同じ。例: 積水樹脂） |

拾えない形:

- 一覧を JavaScript で組み立てるページ（ソフトバンク、任天堂）
- eir-parts でも標準のフィードに資料が入っていない会社（キオクシア）
- これらは設定に載せていない。リクルートは過去分が JavaScript の中にあり、HTML に出ている最新の 1 本だけ拾う

#### 保存と重複

- 保存先とファイル名は TDnet のジョブと同じ。同じ日・同じ表題のファイルが既にあれば、名前の末尾に番号の後ろ 6 桁を付ける
- 索引は source=`homepage`、storage=`local`。索引にある PDF は取りに行かない
- PDF を取ったら sha256 を出し、同じ会社の索引（出所を問わない。TDnet・IR BANK も）に同じ中身の資料があれば保存しない（`dup`。KAB-439）。番号を ledger の `dup` に残し、次からは取りに行かない
- 同じ PDF を表題を変えて複数の分類に載せる会社がある（丸紅の「第1四半期 IR資料」「IR資料_2026年3月期第1四半期」「プレゼンテーション資料 2025年8月1日」は同じファイル）

#### `--years N`（KAB-429）

今日から N 年前の日より前の資料を保存しない。

| 資料 | 扱い |
|---|---|
| eir-parts | フィードの日付で決める |
| 会社のページで、表題か URL にある一番新しい年（西暦 4 桁、平成・令和）が N 年前の年より古い | 取りに行かない（`old` に数える） |
| 会社のページで、年が読めない、または年が N 年前の年と同じ | 取ってから公開日で決める。古ければ保存しない（索引に無いので次の実行でもまた取る） |

- `--dry-run` でも年で外した分は `old` に数える。取ってから決める分は保存する側に数える
- `--ledger PATH` は、取ってから公開日で `--years` より前と分かった資料の番号と公開日を JSON に残し、次の実行では取りに行かない（KAB-438）

#### ledger

- 形は `{"old": {番号: 公開日}, "checked": {code 5 桁: 時刻}, "dup": {番号: 中身が同じ索引の番号}, "stale": {code 5 桁: stale に出した短信の時刻}}`
- ledger が無い・壊れていれば空から。`--dry-run` では書かない

#### 回の種類と使い分け

cron は 2 本。

| 回 | 見る会社 | 使い分け |
|---|---|---|
| 毎時（短信の取り込み `xbrl_statements` → `tanshin_covers --days 1` → `tdnet_briefing_r2 1` のあと）。`--pending` 付き | 下の `--pending` の会社だけ | 短信が出た直後の資料 |
| 日曜。`--pending` 無し | 設定の全社 | 短信から日がたって載る質疑応答などの拾い直し |

`--pending N`（KAB-438）は、設定の会社のうち、次を両方満たす会社だけを見る。

- 直近 N 日の最新の短信の表紙（`tanshin_covers`）が「補足説明資料 有」か読めない
- 短信の開示の 2 日前以降の公開日の資料が、会社ページから索引に入っていない

| 最後に見た時刻（ledger の `checked`） | 扱い |
|---|---|
| その短信のあとにまだ見ていない | すぐ見る |
| その短信のあとに見た | 20 時間（`RECHECK`）たってから見る |
| 一覧が取れなかった | 残さない（次の回にまた見る） |

`--pending` も `--only` も無い回は、次の 2 つを出す。

| 行 | 出す会社 | 意味 |
|---|---|---|
| `stale <code> <開示時刻> <new\|known> <表題>`（KAB-442） | 設定にある会社のうち、最新の短信（直近 120 日）が「資料 有」で、開示から 30 日（`STALE_DAYS`）たっても、短信の 2 日前以降の公開日の資料がどの出所からも索引に無い | フィードの番号や載せる場所が変わった疑い。遅れて載せる会社の資料は週 1 回の全社の回で入るので、30 日あれば外れる |
| `missing <code> <開示時刻> <表題>` | 直近 7 日（`MISSING_DAYS`）に「資料 有」の短信を出したのに、どの出所の資料も短信の 2 日前以降に索引に無く、設定にも無い | 設定に足す候補 |

- stale の最後に `homepage_briefing stale_new=N stale_known=M`
- `stale_new` は ledger の `stale` にまだ無い（会社・短信の組が初めての）数。1 以上の行は `deploy/alert_patterns.py` のアラートになる。終了コードは変えない。ledger が無いと毎回 new になる

#### 引数と終了コード

- `--only` は設定にある code だけ
- `--limit` は 1 社で新しく保存する件数（ページの上から）
- `--dry-run` は新しく保存する分を出すだけで、PDF も索引も書かない
- 最後の行は `homepage_briefing stopped=<done|disk_low> listed=… saved=… skipped=… old=… failed=… dup=…`

| 条件 | 扱い | 終了コード |
|---|---|---|
| `--only` に設定に無い code | 止まる | 2 |
| PDF を取る直前の `shutil.disk_usage(--dest).free` が、IR BANK の一括取得と同じしきい値（`MIN_FREE_BYTES`）未満か読めない | 保存せずに止まる（`stopped=disk_low`） | 0 |
| 失敗が 1 件でもある | — | 1 |
| `stale_new` が 1 以上 | アラート | 変えない |

### OneDrive へ移す（`briefing_offsite.py`）

ジョブ: [market/briefing_offsite](gen/market.md#marketbriefing_offsite)（`jobs/market/briefing_offsite.py`。KAB-345）

- 会社（索引の `code`）ごとに、資料の日付の最新から 1 年以上前の PDF だけを移す。最新の日付そのものは残る。別の会社の日付では切らない
- 資料の日付はファイル名先頭の `YYYYMMDD`、取れなければ `pubdate`
- 置き場所は backup と同じ `BACKUP_OFFSITE_REMOTE` / `BACKUP_OFFSITE_PATH`（既定 `onedrive:stock-analyzer-backups`）の `data/briefing/` の下。相対パス（`rel_path`）は変えない
- 対象はバッチごとに取り直す。1 回の rclone は `BATCH_SIZE` 件（100。50〜200 のあいだ）を `copy --files-from`（`--transfers` は `TRANSFERS`=4）で送り、`check --one-way` で確かめる（KAB-355）

| `check --one-way` の結果 | 索引 | 手元 |
|---|---|---|
| 一致した（copy が非ゼロでも） | `storage=onedrive` にする | そのあと削除する |
| 一致しないファイル | 変えない | 変えない |
| check 自体が失敗したバッチ | 変えない | 変えない |
| 移し済みで手元だけ残っている | 同じバッチにもう一度載せる | 一致を確かめてから消す |

- 行は消さない（`status` も `rel_path` も残す）。途中で落ちても、同じコマンドの再実行で続きから揃う
- `--dry-run` は対象を出すだけ。`--limit` はこの実行で手を付ける最大件数。何度流しても、移るものは移った状態に揃う
- 毎日 1 回、`deploy/crontab` から `deploy/cron-run.sh` で動く（KAB-353）
- 同じジョブが既に動いているときは移さず終わる（終了コード 0）。ロックを持たない、先に手で始めたプロセスも対象なので、全件移動の途中で cron が来ても二重には移さない
- 手では `deploy/cron-run.sh jobs/market/briefing_offsite.py --dry-run` のあと、`--limit` を付けて少しずつ
- 読む側は `app/market/briefing_files.open_briefing`。`storage=onedrive` は rclone で一時ファイルにして読み、`data/briefing` には戻さない。`analyzer/agent_forecast/fetch_docs.py` の説明資料と、下の読み取りジョブは、索引にあればここから取る
- 保存済み判定（`has_saved` / `saved_document_ids`）は `storage` を見ない

### 決算説明資料の読み取り（`briefing_extract.py`）

ジョブ: [market/briefing_extract](gen/market.md#marketbriefing_extract)（`jobs/market/briefing_extract.py`。KAB-349）

- `briefing_documents` の PDF を 1 ページずつ Claude（Haiku 5.5）に読ませ、ページのタイトル・要約・表（期間の列 × 行。行に内訳の親 `of`）を返させる
- Message Batches API（通常の半額。終わるまで最長 24 時間）で出す
- 規則と試験は `analyzer/deck_reader/`（`results/` に費用・精度の記録）
- 本番のコードは `app/market/briefing_extract.py`（文字データ・分類・プロンプト deck-v3・照合・検算）と `app/market/briefing_batch.py`（出す・受け取る・書く）
- PDF は `open_briefing` で開く。手元（`storage=local`）は `data/briefing`（`SHARE_ROOT` があればその下）。`storage=onedrive` は rclone で一時ファイルにして読み、`data/briefing` には戻さない
- `ANTHROPIC_API_KEY` が要る（従量課金）。手での実行は `deploy/README.md`。cron とデプロイでは走らない

#### `submit`

まだ読んでいない資料（`briefing_extracts` に行が無い）を開示の新しい順に出す。

| 資料・ページ | 扱い |
|---|---|
| 同じ開示番号が複数の出所にある | tdnet → irbank → homepage の PDF を読む |
| 表題に台本つきの語（ノーツ付・ノート付・スクリプト付・台本付。`is_script_title`）がある | 読まない。スライドだけの版と同じスライドなので。PDF と索引は残す。台本つき版しか無い会社の分は読まれない |
| 表紙・中扉・注意事項のページ（文字が少ない・注意事項の語） | 出さない |

- 依頼は 1 ページ 1 件（入力が 10 万トークンを超えると単価が上がるので丸ごと 1 件にしない）
- 画像は 110dpi の PNG、文字データを添える。システムプロンプトはキャッシュに置く。effort は low。`--no-thinking` で thinking を止める（未検証）
- 1 Batch は 5000 件・150MB まで
- 出した資料は `briefing_extracts` に status=`submitted`（batch_id、custom_id の頭 `request_key`）
- `--redo` は読み取り済みも出し直す。`--dry-run` は依頼の数を数えるだけ

#### `collect`

`submitted` の Batch を見て、終わったものだけ書く（終わっていなければ次の実行で）。

1. PDF の文字データを読み直し、数字が文字データにあるか（`verified`）と内訳の合計（`sum_status`）を確かめる
2. `briefing_pages`（ページごと。Claude に回さないページも kind だけ）と `briefing_facts`（表を 1 セル 1 行）を資料ごとに消して書き直す
3. トークンは `briefing_extracts` に（入力はキャッシュの読み書きを含む）

| 結果 | `briefing_extracts` の status |
|---|---|
| 1 ページでも読めた | `done` |
| 全部だめ、または PDF が無い | `failed` |

Claude の応答の形:

| 応答 | 扱い |
|---|---|
| 1 枚のスライドを複数に分けて `[{...}, {...}]` や `{...}, {...}` で返した | 表をつないで 1 ページにする（題名は ` / `、要約は空白でつなぐ） |
| オブジェクトの後ろにそれ以外の余分がある | 捨てる |
| `tables` が無いまま閉じている | 括弧の閉じ過ぎとみてそのページを error にする |
| JSON が読めない | Claude の応答を `briefing_pages.raw_text` にそのまま残す（Batch の結果は 29 日で消えるので、原因はここで見る。API には出さない） |

同じ値の 2 行（KAB-396）:

- 英語と日本語を並べた資料の `$ 4.1B / 41億ドル` のように、同じページ・行名・区分・期間・種類で、単位の倍率（億・十億・10億・兆・百万・千・B・M など）を直すと同じ値になる行は 1 行にする
- 倍率を除いた単位（円・ドル）が同じものだけ比べる。範囲（`8.7-9.5`）は数字を全部比べる
- 残すのはそのページで多く使われている単位、同数なら倍率の小さい単位
- 検算はまとめたあとの行で行う

ログ:

- 資料ごとの完了行は読めなかったページ数を `unparsed=` で出し、続けてそのページ番号と理由を 1 行ずつ出す
- `errors=` は Slack に page されるので使わない。資料ごと落ちたものは `failed=` で page される

#### 照合と検算

照合は「ページのどこかにその数字がある」しか見ない。別の項目・期間に付けた誤りは見つからない（検算と画面の表で補う）。グラフが画像で貼られた資料は文字データに数字が無く `verified` が付かない（OCR の照合は analyzer の PoC だけ）。

検算の約束（`check_sums`）:

| 場合 | 扱い |
|---|---|
| 同じ行が 2 つの表に出た | 1 つにして足す |
| 同じ親に別の切り口の内訳が 2 組（部門別と地域別など） | 2 組に分けて確かめる（`split`） |
| 率の列・行 | 見ない |
| 内訳がすべてプラスで合計に届かない | 一部だけの内訳（`partial`。主要製品だけの表など） |
| 合わない | 括弧をマイナスとみなして試す（`paren`）。括弧の意味は資料で違う（マイナス・前回予想・率）ので、値は書き換えない |

許容は一番粗い表示桁の半分 × (内訳の数 + 1)。

## PTS・分足

### PTS ランキング（`pts_snapshots`）

ジョブ: [market/pts_snapshot](gen/market.md#marketpts_snapshot)（`jobs/market/pts_snapshot.py`）

株探 `https://kabutan.jp/warning/` の `pts_night_price_increase` / `pts_night_price_decrease` / `pts_night_volume_ranking`（夜間 PTS。株価は 15 分ディレイ）。取得は `app/market/pts.py`。キー不要。画面は `docs/screen/pts.md`。

- 1 ページ 15 銘柄。各ランキング 5 ページ（75 銘柄）まで。リクエストの間は 3 秒
- 1 ページ目の見出し「通常取引 D日終値」の D を、今日以前で一番近いその日にして `session_date` にする。夕方の開始前や休日は前のセッションのまま出るため
- 前夜の最終（株探のナイトタイムは 06:00 まで）は翌日 15:30 の回が拾う
- 翌営業日の始値は `daily_prices` の `session_date` より後の最初の日足の `open`（調整前）。19:30 の日次バッチの後に埋まる

| 条件 | 扱い |
|---|---|
| 1 ページ目の「HH:MM現在」が同じセッション・ランキングで保存済み | 残りのページを取らずに飛ばす |
| 3 本とも取得に失敗 | 非ゼロ終了 |

### 分足（`data/intraday/1m/`）

ジョブ: [market/intraday_1m](gen/market.md#marketintraday_1m)（`jobs/market/intraday_1m.py`。KAB-208）

yfinance の 1 分足。DB には入れず、日ごとの Parquet（zstd）を `data/intraday/1m/YYYY/YYYY-MM-DD.parquet` に置く。取得と読み出しは `app/market/intraday.py`。キー不要。今は保存だけで、API・画面・analyzer からは読まない（1 分足の出来高が日足と合わないため。下の注意）。

- 1 ファイルに内国株の全銘柄。列は `code`（J-Quants 5 桁）/ `ts`（Asia/Tokyo）/ `open` / `high` / `low` / `close` / `volume`
- Yahoo のシンボルは 5 桁の先頭 4 文字 + `.T`（`72030` → `7203.T`、`130A0` → `130A.T`）
- 1 分足は 1 回 7 日まで、さかのぼれるのは約 30 日。毎回直近 7 日を取り、まだファイルに無い (銘柄, 日) だけ書く
- cron は 19:30 の日次バッチの後。当日の AdjFactor が DB にある状態で過去の日を埋めるため

書くかどうか（値は調整前。Yahoo は分割があると過去の分足をさかのぼって直すので、一度書いた (銘柄, 日) は上書きしない）:

| (銘柄, 日) | 扱い |
|---|---|
| 既にファイルにある | 上書きしない |
| その回で一番新しい日 | 常に書く |
| それより前の日で、DB の日足がその日まであり、間に `adj_factor ≠ 1` が無く、分足の高値と日足の高値の比が 1.2 以内 | 書く |
| それより前の日で、上に当たらない | 書かない |

読むとき:

- `daily_prices.adj_factor` を、その日より後の分を掛けて調整する（`load(..., db=...)`）
- 出来高は割り、ライツイシュー（ExRT=3）の係数は使わない。`app/market/adjust.py` と同じ向き。丸めない

取得と終了コード:

- 1 銘柄ずつ 0.5 秒あけて取る（全銘柄で約 45 分）

| 応答 | 扱い |
|---|---|
| 429 | 60 秒・300 秒待って取り直す。それでも駄目なら打ち切って残りは次の回 |
| 1 分足を配っていない銘柄（応答に `tradingPeriods` が無い） | 空として数える |
| 取れた銘柄が 0、または失敗が 1 割を超えた | 非ゼロ終了 |

注意:

- Yahoo の東証 1 分足はザラバの値は証券会社のチャートと合う
- 最後の足は 15:24 で、15:30 のクロージング・オークションの約定（引け値と大きな出来高）が無い。15:25〜15:29 は注文を受けるだけで約定が無いので、足が無くてよい
- 寄りの数分（09:00〜09:04）が無い日も多い
- 出来高は 1 本の足に数百万株の塊が入ったり、寄り・引けの分が抜けたりする。1 日の合計が日足（J-Quants と yfinance の日足は一致）と合わない日が多い。トヨタ 2026-10-19〜27 の 7 日で、合計は日足の 0.65〜2.1 倍。10/26 は 09:00 の 1 本が 2,487 万株で、日足は 1,720 万株
- 日足の始値・終値・出来高とは合わない。日足は `daily_prices` を使う
- 約定の無い分は足が無い。同じ足を SQLite（主キー込み）に入れるとファイルの数倍だった

## ホームページ URL・短信の表紙

### ホームページ URL（`homepages`）

ジョブ: [market/homepages](gen/market.md#markethomepages)（`jobs/market/homepages.py`）

企業マスタの各社について、会社ホームページの URL を 1 本残す。IR ページとの区別はまだしない。API / SPA / cron はまだ無い。表は `jobs/migrate.py` が作る。

| 使い方 | 扱い |
|---|---|
| `python jobs/market/homepages.py --code 4543 --dry-run` | 計画を出すだけ |
| 手入力 `--set 7203 https://…` | `source=manual`。`--refresh` でも上書きしない |
| 束 `config/market/homepage_manual.json` を `--set-file` で入れる | `--dry-run` 可。本番にはまだ入れない。HTTP はしない |
| `--all` | 本番投入は承認後。今の stocks だけ対象 |
| `python jobs/market/homepages.py --all --compare-hosts` | ホスト一致の確認。書き込みしない |

コードは J-Quants の 5 桁普通株。CLI と XBRL / 短信の code は `ordinary_code` で畳む（4 桁は末尾 0、優先株は先頭 4 文字 + 0）。

#### 候補の選び方

確度は source で一意なので列にしない（manual > xbrl_url > tanshin_html）。

| source | 出どころ | origin | 備考 |
|---|---|---|---|
| `manual` | `--set` / `--set-file` の手入力 | `manual` | 最上位。自動更新では触らない |
| `xbrl_url` | 最新 1 件の `tse-ed-t:URL` / `tse-re-t:URL` | `xbrl:{doc_id}` | スキーム無し（www. 無しも）と全角コロンを直す |
| `tanshin_html` | 下の条件の決算短信 `tdnet_pdf_documents.html_gz` | `tanshin:{disclosure_no}` | 『URL』『ホームページ』ラベル近くの URL を優先。監査法人・親会社の URL を先頭だけ見ると取り違える。w3.org / TDnet / EDINET / JPX / SEC / SNS はホストの完全一致かドット区切りの末尾一致で捨てる（86970 の `jpx.co.jp` は残す） |

| 状況 | 扱い |
|---|---|
| 自動の候補が複数 | 提出の新しさ優先。同じ日なら source の順 |
| XBRL | code ごとに `submitted_at` 最新 1 件だけ読む。その URL が捨てられれば XBRL 候補なし（古い提出へは下がらない） |
| 有効な短信より XBRL の提出が古い | 短信を採る |
| 再実行 | UPSERT。値が同じなら `checked_at` だけ進める |

短信の候補:

- 対象は題名に「決算短信」または `FinancialStatements` を含むもの、および `statements.type_of_document` に `FinancialStatements` を含むもの（題名が本文の切れ端でも種類の列で判定）
- 新しい順に、URL が取れた最初を採る
- 直近の数本から URL が取れない会社は、そこで打ち切らず取れるまで古い短信へさかのぼる（表紙が空の変換や文字の無いパスだけの短信より、URL の書いてある古い短信を採る。KAB-322）

| 短信 | 扱い |
|---|---|
| 題名に「訂正」がある | 飛ばす |
| 変換後 HTML（`bytes`）が 5KB 未満 | 飛ばす |

#### URL の読み方

| 入力 | 扱い |
|---|---|
| スキームの無いドメイン（`www.toray.co.jp`、`wize-inc.jp`） | `https://` を補う |
| 本文中のスキーム無し | 『URL』『ホームページ』の近くだけ拾う |
| XBRL の値でラベルが無い | `normalize_url` が受ける |
| pdfium が `(cid:N)` にした欧文（`N + 29` が印字 ASCII） | 戻してから探す |
| URL 中の空白（`http:// www.example.co.jp`） | 除く |
| `http://https://` のような二重スキーム | 内側を残す |
| 末尾ラベルが ICANN の TLD でないホスト（`www.prologis`） | 捨てる。同じ短信の次の URL、無ければより古い短信へ進む |
| `.toyota` などブランド gTLD | 残す |
| `jpx.co.jp` | 捨てる。例外は日本取引所グループ（86970）自身 |

持株会社化で書類が旧コードに残る会社は `PREDECESSOR_CODES`（641A0←72400、642A0←95080、590A0←44490）。新しいコードに書類が無いときだけ旧コードを見る。

#### 将来

- 将来の IR / 月次ページは `homepages` に足さず、別表（`code`, `kind`, `url` の複数行）にする
- 月次ページ URL の正本は `config/monthly/ir_monthly.json` の `index_url`。KAB-320 で `homepages.host` と突き合わせる
- 上場廃止会社の行は残す（stocks も消さない）

### 短信表紙の説明資料（`tanshin_covers`）

ジョブ: [market/tanshin_covers](gen/market.md#markettanshin_covers)（`jobs/market/tanshin_covers.py`。KAB-325）

決算短信1ページ目の「決算補足説明資料作成の有無」「決算説明会開催の有無」を、短信ごとに1行残す。

- 主キーは `disclosure_no`。`code` にインデックスを付ける。最新の1件は `disclosed_at` が新しい行。表は `jobs/migrate.py` が作る。API / SPA はまだ無い
- 前の短信の行は消さない
- 説明資料 PDF の保存は `jobs/market/tdnet_briefing_r2.py`（KAB-327、`briefing_documents`）。有無の判定とホームページ探索は、その索引を見て別 PR で足す

| 引数 | 対象 |
|---|---|
| `python jobs/market/tanshin_covers.py --code 7203` | その会社 |
| `--limit` | DB に既にある短信から新しい順にその社数だけ |
| `--days N` | 今日（JST）を含む直近 N 日に決算短信を出した会社について、各社の最新1件を書く |

- `--code` / `--limit` / `--days` はどれか 1 つ。`--all` は無い
- 日次の回は XBRL の最後の回のあと（KAB-416）
- 毎時の短信の取り込みのあとにも回す（KAB-438。会社ページの説明資料の `--pending` が読む）
- 初回は `--days` を 1 四半期より長くして手で一度（`deploy/README.md`）

対象の銘柄と短信:

| 銘柄・短信 | 扱い |
|---|---|
| 普通株（`stocks.product_category` が内国株券 `011` または外国株券 `021`）と REIT（`013`） | 対象 |
| ETF（`014`）と外国 ETF / ETN（`023`） | 除く |
| REIT で、名称に「ファンド」または「インフラ」がある投資法人 | 除く |
| マスタが無い銘柄 | 題名が ETF・ETN・投資信託・ファンドでなければ残す |
| 題名に「決算短信」がある | 対象 |
| `訂正`、「決算短信…の修正について／の一部修正」 | 飛ばす |

どの書類を読むか（書くのは各社の最新の提出日だけ）:

| その日の書類 | 読むもの |
|---|---|
| PDF（`tdnet_pdf_documents`） | 変換 HTML の1ページ目 |
| XBRL | サマリー iXBRL（表紙に相当） |
| 両方あり、片方だけ有無が読めた | 読めたほう |
| 両方あり、どちらも読めた | 時刻が新しいほう。同じ日のもう一方の `disclosure_no` は残さない |

値:

| 表紙 | 値 |
|---|---|
| 有 | 1 |
| 無 | 0 |
| 文言が無い | NULL |
| 画像だけ | NULL |
| 「有・無」と両方印字されてどちらとも取れない | NULL（丸印の位置までは見ない） |

- 説明会の括弧は、投資家・アナリストなど対象が読めるときだけ `briefing_note`。単位の注記や、列の並びに紛れた「氏名」は残さない
