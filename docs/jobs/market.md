# 取込と保存の規則: 株価・決算

ジョブの起動時刻・引数・読み書きする表は [ジョブ設計](gen/market.md)（自動生成）。ここには規則だけ書く。

## 日次バッチ (19:30 JST)

1. `/equities/master` で銘柄マスタ更新 (ProdCat 含む)
2. `/equities/bars/daily?date=当日` + `/indices/bars/daily/topix` 取込。戻り値は全部保存する。
   AdjFactor ≠ 1（権利落ち日）の銘柄は、その銘柄の調整後の値を DB の中で計算し直す（`app/market/adjust.py`）。
   権利落ちで過去の Adj* がさかのぼって直るので、差分だけでは DB の過去分が古いまま残る。API で取り直すと
   プランの期間より前が取れないので、後から変わらない調整前の値と AdjFactor から J-Quants と同じ手順で出す
3. `/fins/summary?date=当日` と `/fins/summary?date=前営業日` 取込。前営業日は DB の株価で当日より前の最終日
4. indicators 再計算 (MA/52週/RS/ステージ。RSユニバースは内国株券のみ) → earnings_events 更新。
   2〜3 が 0 件（前営業日は新しく増えた件数）で indicators が株価の最終日まで済んでいれば飛ばす。
   指標は全件計算し、今の行と突き合わせて変わった行の upsert と消えた行の削除だけ書く（全件の削除と入れ直しは本番規模で数分かかる）。
   書き込みはチャンクごとに commit する（1 本のライターロックを `busy_timeout` 以上握らない）
5. `/fins/earnings-date?date=当日` と `date=昨日`。昨日は暦日。4 の後。株価が 0 件でも行う。失敗しても 4 の結果は残し、プロセスは非ゼロで終わる

定常の夜は **計7リクエスト**。内訳はマスタ、株価、TOPIX、財務の当日、財務の前営業日、発表予定の当日、発表予定の昨日。発表予定の2本は `/fins/` の枠。台帳が空の初回は指標のあと PubDate を `EARNINGS_DATE_LOOKBACK_DAYS` 日さかのぼる。この歩きは 7 に入らない。決算開示は15時以降が多いため、当日夜(19:30)に1回実行する。
19:30 より後の開示（全体の 0.3% 程度）は、翌営業日の回が前営業日の決算を取り直して拾う。

J-Quants の応答には、当期の日付（`CurPerSt` / `CurPerEn` / `CurFYSt` / `CurFYEn`）が 1 年前になっている開示がある（KAB-454。API を引き直しても同じ）。規則では直さず、短信の原本などで確かめた開示だけを `config/market/statement_period_fixes.json`（開示番号・J-Quants が返す誤った期末 `period_end`・根拠）に載せる。`statements` の行を作る `_statement_row` が、開示番号が一覧にあり、応答の `CurPerEn` が一覧の期末と同じときだけ `period_start` / `period_end` / `fiscal_year_start` / `fiscal_year_end` を 1 年進める（月末は翌年の同じ月の月末）。期末が一覧と違えば直さず `WARNING jquants statements period-fix-mismatch` を出す（J-Quants が直したら一覧から外す）。`raw_json` は応答のまま。日付ごと・銘柄ごとの取込とバックフィルはどれも `_statement_row` を通る。短信 XBRL から作った行（`source='xbrl'`）の日付は J-Quants の取込で上書きしない（`docs/jobs/xbrl.md` §3.2）ので、その行は一覧に載せても日付が変わらない。

日次の回は、J-Quants から初めて入った開示（XBRL から作っただけの行を含む）のうち、提出日が期末から 300 日以上あとで、通期は `NxtFYSt` が `CurFYEn` の翌日でないもの、四半期（1Q〜3Q の短信）は同じ銘柄・同じ期（`type_of_period`・`period_end`）の前の短信と売上高が違うものを `WARNING jquants statements period-candidate` で出す（直さない。一覧の開示は除く）。四半期の条件は 1 年後の出し直し（訂正）も拾うので候補にとどまる。1 つの開示で出るのは J-Quants から初めて入った回だけなので、確認済みの一覧は持たない（バックフィルと前営業日の取り直しでは同じ開示を出し直さない）。候補は原本の期の印字で確かめ、誤りなら一覧に足して既存の行を `tools/market/fix_statement_periods.py` で直す（手順は `deploy/README.md`）。

冪等設計 (UPSERT) のため何度実行しても安全。バックフィルは株価と決算を同じ日に commit できた日付を
スキップするので、中断しても再実行すれば続きから埋まる。カレンダーが空、またはその日が失敗したら非ゼロ終了。

`run_batch.py --refetch-prices` は DB にある日足を日付ごとに全部取り直し、指標を計算し直す。
対象は UL / LL が NULL の行が残る日だけなので、中断しても再実行で続きから。失敗した日は飛ばして最後に非ゼロ終了。
1 日 1 リクエストで、5 年分なら約 1,250 日 ≒ 20〜40 分。プランの期間より前の日は 400 で失敗のまま残る
（その日の調整後の値は `--readjust-prices` で直る）。

`run_batch.py --readjust-prices` は全銘柄の調整後の値を DB の中で計算し直し、指標を計算し直す。API は呼ばない。

## 決算の数字の扱い

### Q単独値の算出 (保存せずAPI側で導出)
`Q単独 = 当期累計 − 前四半期累計`(1Qはそのまま)。前年同期のQ単独と比較しYoY成長率を計算。EPSも同様。

`CurPerType` は `1Q/2Q/3Q/4Q/5Q/FY` を取る。FYと4Qは通期累計として同一視。
5Q(決算期変更等の変則期)は前後関係が定義できないためQ単独を算出しない。

QoQ は `Q単独(当四半期) vs Q単独(直前四半期)`。1Qの相手は前事業年度の4Q単独になるため
季節性が断絶する(UI側で注記する)。算出しないと5月の決算集中日にQoQ列が全社空欄になる。

### 速報一覧に出す開示 ★注意
予想修正のみの開示(`EarnForecastRevision` 等)は実績値が空なので除外する。

遅延企業は過去の期をまとめて再提出することがあり、そのまま出すと1銘柄で何行も並ぶ。
既出期間の再提出は **売上・営利・経利・純利が初回と一致するものを除外**し、
損益が変わったものは「訂正」として残す。**EPSは比較に含めない。**
分割・増資で遡及修正されるだけのことがあり、訂正扱いにすると
損益が変わっていない再提出まで一覧に残ってしまう。

複数期を同時に「初めて」開示する日があるため、1銘柄1行には潰さない。
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
同一事業年度の、発表日が前の開示の通期営業利益予想 (`statements.fy_operating_profit`) と比較し、
±0.1%以上 (`EARNINGS_PARAMS["revision_noise_th"]`) なら一覧の時刻列に `上方` / `下方` を出す。
ホバーに修正率。判定は営業利益だけ。売上・経常・純利益が逆向きでも営利に従う。
直前が 0・欠損、当期が欠損、閾値未満はマークなし。赤字予想は `|直前|` を分母にし、
損失縮小を上方にする。同日の予想修正のみの開示 (`is_revision`) は直前に使わない
（短信と `EarnForecastRevision` が同日だと新しい値同士で消えるため）。
既出期間の損益訂正 (`restated`) は向きを付けず `訂正` のまま。両方あれば両方出す。

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

TDnet 決算説明資料 PDF（`jobs/market/tdnet_briefing_r2.py`。KAB-327）:

- 一覧は `jobs/_tdnet.py` の `fetch_days`。採否は `app/market/briefing_title.py` の `is_material_title` だけ（保存も、あとから同じ判定が要る処理もここを呼ぶ）
- 採る: 空白と全角半角を畳んだ表題が、決算・四半期・中間・通期・業績のどれかと、説明資料・説明会資料・補足資料・プレゼン資料（プレゼンテーション資料を含む）・質疑応答（主な質疑、Q&A。要旨・概要だけでは採らない。KAB-336）のどれかを両方含むもの。英語は earnings / financial results の presentation、または briefing / supplementary materials、または Q&A / questions and answers。決算短信の補足資料は採る
- 落とす: 訂正・誤記。お知らせ、開催・中止・延期、掲載について・公開について（決算説明会開催のお知らせ、説明資料を載せた案内）。英語の notice / correction。資料の語も質疑応答の語も無い「決算説明会」。決算短信そのもの。成長可能性の説明資料や、決算などの語が無い補足説明資料。中期経営計画の説明会資料（決算・四半期などの語が無いもの）
- 「修正」だけでは落とさない。補足資料の表題に業績予想の修正が入ることがある
- 保存先は `data/briefing/{code}/YYYYMMDD_CODE_名前_表題.pdf`。`{code}` とファイル名は月次 PDF と同じ（display_code。J-Quants の 5 桁にはしない）。`SHARE_ROOT` を置くとその直下の `briefing/`
- 既存ファイルが PDF なら取り直さない。索引が無ければ索引だけ足す。その件数は `reindexed`（新規に保存した `saved` とは別）。PDF でなければ取り直す
- 索引は `briefing_documents`。列は source / document_id / code（5 桁）/ company_name / pubdate / title / rel_path（`data/briefing` から）/ url / sha256 / size / status / storage（`local` / `onedrive`。既存行は `local`。KAB-345）/ saved_at。このジョブは source=`tdnet`、document_id は TDnet の PDF ファイル名、status=`saved`、storage=`local`。ホームページで見つけた PDF は同じ表に source=`homepage`（取得はこのジョブではしない。`app/market/briefing_store.record_briefing`）。IR BANK の過去分は source=`irbank`（下）。`storage=onedrive` の行は手元が無くても取り直さない
- 毎時（12–23 時）の短信の取り込みのあとに当日分（`tdnet_briefing_r2.py 1`。KAB-438）。日次 cron は直近 7 日（22:10 JST。取りこぼしの拾い直し）。初回の約 30 日は `deploy/cron-run.sh jobs/market/tdnet_briefing_r2.py 30` を手で一度。デプロイでは走らない
- 日次の `backup_data` には載せない（コピーだと手元が残る）。1 年以上前の PDF は下の `briefing_offsite` が移す。TDnet は約 1 か月で PDF を消す

IR BANK の過去の決算説明資料（`tools/briefing/irbank_ingest.py`。KAB-330）:

- TDnet が消した分を一度だけ手で取る。cron には入れない。毎日の分は上の `tdnet_briefing_r2.py`
- 対象は普通株（`stocks.product_category` が内国株券 `011` または外国株券 `021`）と REIT（`013`）。ETF（`014`）と外国 ETF / ETN（`023`）は除く。REIT でも名称に「ファンド」または「インフラ」がある投資法人は除く。区分が空の銘柄は、名称がファンド・投資信託・ETF・ETN でなければ残す。KAB-325 の `tanshin_covers` と同じ考え方
- 一覧は `https://irbank.net/{4桁}/ir` を 1 社 1 回。同じ 4 桁の株は末尾 0 の行を代表にする。表題の採否は `is_material_title` だけ。PDF は `https://api.irbank.net/api/market-files/v1/tdnet/{18桁の末尾14桁}.pdf`（`tools/tdnet_pdf/ingest.py` と同じ）
- 保存先とファイル名は TDnet のジョブと同じ（`data/briefing/{4桁}/YYYYMMDD_CODE_名前_表題.pdf`）。索引の code は 5 桁、document_id は 18 桁の開示番号。同じ番号、またはその末尾 14 桁が、どの source でも索引にあれば取り直さない
- 読み終えた銘柄は `briefing_irbank_scans`（code / scanned_at / listed / saved / skipped / reindexed / missing / failed）。429 / 503 で止めた銘柄、空き不足で止めた銘柄、PDF の取得に失敗した銘柄は行を書かないので次の実行が続きから。PDF を書く直前に `shutil.disk_usage(--dest).free` を見て、25GiB（`MIN_FREE_BYTES`）未満ならその PDF は書かず `stopped=disk_low`、終了コード 0（KAB-355）。一時ファイルの直前も同じ。同じ `code` の既存索引と今回の一覧を合わせた最新から 1 年以上前の PDF は `data/briefing` に置かず、一時ファイルからその会社の分だけ下のバッチで OneDrive へ送り、一致したものだけ索引を最初から `storage=onedrive` で書く。最新そのものは手元に残す。PDF が 404 だけの銘柄は行を書く。成功した PDF は索引にあるので取り直さない。`--rescan` を付けるとこの表を無視して一覧を読み直す（`--code` と `--all` のどちらとも。KAB-338）。索引にある PDF は取り直さず、新しく対象になったものだけ保存する。付けないときの対象は変わらない
- 一覧の SELECT は読み終わったら閉じ、PDF の取得中はトランザクションを持たない。索引（`briefing_documents`）と進捗（`briefing_irbank_scans`）の UPSERT は `run_sqlite_write` で locked を数回、待ちを伸ばして再試行する（KAB-305 と同じ。KAB-332）
- `--limit` はこの実行の社数。`--sleep` は請求の間隔（既定 1 秒）。`--dry-run` は一覧だけ見て、PDF も索引も `briefing_irbank_scans` も書かない（見た社は残件のまま）。`--rescan` でも `remaining` は scans が無い社数のまま（読み直した社は残件に入らない）
- 手での実行は `deploy/README.md`。デプロイでも cron でも走らない

会社の IR ページの決算説明資料（`jobs/market/homepage_briefing.py`。KAB-413）:

- TDnet に説明資料を出さない会社の分（例: アルバック）。対象は `config/market/briefing_pages.json` の `issuers`（code / name / index_url、任意で link_re）。index_url は資料が並ぶ一覧ページ。過去分が別のページにある会社は配列で複数書く（同じ資料が両方にあれば 1 本）。転送や meta refresh で年度のページへ飛ばす会社は、飛ぶ前の URL を書く（ジョブが 2 回までたどる）。TDnet に資料を出す会社は載せない（開示番号が無いので TDnet の行と 1 つにまとまらず、同じ資料が 2 行になる）
- 一覧ページの `<a href>` のうち、パスが `.pdf` で終わり、リンクの文字が `is_material_title` を通るもの。一覧は決算の資料置き場なので決算の語は補って判定し、資料の種類（説明資料・質疑応答など）はリンクの文字に要る。決算短信のリンク、文字の無いアイコンのリンクは採らない。リンクの文字に種類の語が無い会社（例: 日立の「プレゼンテーション:…連結決算の概要」）は、`link_re`（リンクの文字に当てる正規表現）に合うものも採る
- リンクの文字からは、容量や形式の注記（`(1.7MB)`、`[PDF: 548KB]`、`(PDF形式)`、末尾の `PDF`、「新しいウィンドウで開きます」）を落としてから判定と表題に使う。容量の表記が変わっても同じ資料のまま
- リンクの文字が「PDF」だけのときは、直前のリンクか見出しのあとに出た文字（隣の欄の資料名。60 字まで）をリンクの文字として使う（例: リクルート）。リンクの中の `aria-hidden="true"` の要素と、`svg` / `style` / `script` の中の文字は読まない（例: 武田薬品は読み上げ用の文字とアイコンの題で同じ語が 3 回出る）
- 表題は「h2 以下の見出しを上から並べたもの + リンクの文字」（例: `2027年6月期 第1四半期 決算説明資料`）。h1（ページ名）は入れない
- document_id は `hp` + sha1(code 5 桁・表題・PDF のファイル名) の頭 24 桁。URL の途中の番号は資料が足されるとずれるので使わない。会社がファイル名か見出しを変えると別の資料として取り直す
- pubdate は一覧に日付が無いので PDF の更新日時（無ければ作成日時。JST に直す）。どちらも無ければ URL のパスにある YYYYMMDD、それも無いか未来なら取得した時刻。作成日時はひな形の日付のままの会社があるので更新日時を先に見る。会社が PDF を作り直すと開示日より後ろへずれる
- 保存先とファイル名は TDnet のジョブと同じ。同じ日・同じ表題のファイルが既にあれば、名前の末尾に番号の後ろ 6 桁を付ける。索引は source=`homepage`、storage=`local`。索引にある PDF は取りに行かない
- 表題に英文版の語（英文・英語・English。`is_english_version`）がある資料は取らない（KAB-434）。日本語版と中身が同じなので
- `--only` は設定にある code だけ（無い code は終了コード 2）。`--limit` は 1 社で新しく保存する件数（ページの上から）。`--dry-run` は新しく保存する分を出すだけで、PDF も索引も書かない
- `--years N` は今日から N 年前の日より前の資料を保存しない（KAB-429）。eir-parts はフィードの日付で決める。会社のページは、表題か URL にある一番新しい年（西暦 4 桁、平成・令和）が N 年前の年より古ければ取りに行かない（`old` に数える）。年が読めないもの、年が N 年前の年と同じものは取ってから公開日で決め、古ければ保存しない（索引に無いので次の実行でもまた取る）。`--dry-run` でも年で外した分は `old` に数えるが、取ってから決める分は保存する側に数える
- PDF を取る直前に `shutil.disk_usage(--dest).free` を見て、IR BANK の一括取得と同じしきい値（`MIN_FREE_BYTES`）未満か読めなければ、保存せずに止まる（`stopped=disk_low`、終了コード 0）。最後の行は `homepage_briefing stopped=<done|disk_low> listed=… saved=… skipped=… old=… failed=… dup=…`。失敗が 1 件でもあれば終了コード 1
- 一覧を eir-parts（プロネクサスの IR サービス。`ssl4.eir-parts.net`）で出す会社は、会社のページに PDF のリンクが無い。`index_url` に eir-parts のフィード `https://ssl4.eir-parts.net/V4Public/EIR/<4桁>/ja/announcement/announcement_<N>.js`（JSONP。N は会社ごとで 1〜10 に散らばる。同じ中身のフィードを複数持つ会社が多いので、決算資料のいちばん多い 1 本を書く。KAB-430）を書くと、HTML でなくフィードとして読む。採るのは `type` が `pdf` で `news_type` が `ir_material` で始まるもの。会社のページと違って決算以外の IR 資料（事業説明会、個人投資家向け説明会など）も並ぶので、決算の語は補わず、表題そのものが `is_material_title` を通るものだけ。`link_re` も効く（例: 丸紅の「IR資料」「質疑応答」）。短信・適時開示（`tanshin` / `press`）は採らない。同じ決算期・四半期（`fiscal_ym` / `quarter`）に日本語の表題の資料があれば、表題に日本語の無い資料は英文版として採らない（日本語版を出さず英語の表題だけの会社の分は残る）。同じ表題・同じ日の資料が複数の分類に載っていれば最初の 1 本（中身が同じ。例: 積水樹脂）。表題に年度が無ければ `fiscal_ym` から「2027年3月期」を頭に付ける。pubdate はフィードの `date`（時刻が入る行と 00:00:00 の行がある）。document_id は `hp` + sha1(code 5 桁・PDF の URL のパス) の頭 24 桁
- 拾えない形: 一覧を JavaScript で組み立てるページ（ソフトバンク、任天堂）、eir-parts でも標準のフィードに資料が入っていない会社（キオクシア）。設定に載せていない。リクルートは過去分が JavaScript の中にあり、HTML に出ている最新の 1 本だけ拾う
- `--ledger PATH` は、取ってから公開日で `--years` より前と分かった資料の番号と公開日を JSON に残し、次の実行では取りに行かない（KAB-438）。ledger が無い・壊れていれば空から。`--dry-run` では書かない
- PDF を取ったら sha256 を出し、同じ会社の索引（出所を問わない。TDnet・IR BANK も）に同じ中身の資料があれば保存しない（`dup`。KAB-439）。同じ PDF を表題を変えて複数の分類に載せる会社がある（丸紅の「第1四半期 IR資料」「IR資料_2026年3月期第1四半期」「プレゼンテーション資料 2025年8月1日」は同じファイル）。番号を ledger の `dup` に残し、次からは取りに行かない
- `--pending N`（KAB-438）は、設定の会社のうち、直近 N 日の最新の短信の表紙（`tanshin_covers`）が「補足説明資料 有」か読めない会社で、短信の開示の 2 日前以降の公開日の資料が会社ページから索引に入っていない会社だけを見る。最後に見た時刻を ledger の `checked` に残し、その短信のあとにまだ見ていなければすぐ、見ていれば 20 時間（`RECHECK`）たってから見る。一覧が取れなかった会社は残さない（次の回にまた見る）
- `--pending` も `--only` も無い回は、設定にある会社のうち、最新の短信（直近 120 日）が「資料 有」で、開示から 30 日（`STALE_DAYS`）たっても、短信の 2 日前以降の公開日の資料がどの出所からも索引に無い会社を `stale <code> <開示時刻> <new|known> <表題>` で出す（KAB-442。フィードの番号や載せる場所が変わった疑い。遅れて載せる会社の資料は週 1 回の全社の回で入るので、30 日あれば外れる）。最後に `homepage_briefing stale_new=N stale_known=M`。`stale_new` は ledger の `stale` にまだ無い（会社・短信の組が初めての）数で、1 以上の行は `deploy/alert_patterns.py` のアラートになる。終了コードは変えない。ledger が無いと毎回 new になる
- ledger は `{"old": {番号: 公開日}, "checked": {code 5 桁: 時刻}, "dup": {番号: 中身が同じ索引の番号}, "stale": {code 5 桁: stale に出した短信の時刻}}`
- cron は 2 本。毎時（12–23 時）の短信の取り込み（`xbrl_statements`）のあとに `tanshin_covers --days 1` → `tdnet_briefing_r2 1` → `homepage_briefing --years 5 --pending 7 --ledger /var/log/app/homepage_briefing_ledger.json`。日曜 22:25 に `--pending` 無しで全社（短信から日がたって載る質疑応答などの拾い直し）。`--pending` も `--only` も無い回は、直近 7 日（`MISSING_DAYS`）に「資料 有」の短信を出したのに、どの出所の資料も短信の 2 日前以降に索引に無く、設定にも無い会社を `missing <code> <開示時刻> <表題>` で出す（設定に足す候補）。手での実行は `deploy/README.md`

決算説明資料を OneDrive へ移す（`jobs/market/briefing_offsite.py`。KAB-345）:

- 会社（索引の `code`）ごとに、資料の日付の最新から 1 年以上前の PDF だけを移す。日付はファイル名先頭の `YYYYMMDD`、取れなければ `pubdate`。最新の日付そのものは残る。別の会社の日付では切らない
- 置き場所は backup と同じ `BACKUP_OFFSITE_REMOTE` / `BACKUP_OFFSITE_PATH`（既定 `onedrive:stock-analyzer-backups`）の `data/briefing/` の下。相対パス（`rel_path`）は変えない
- 対象はバッチごとに取り直す。1 回の rclone は `BATCH_SIZE` 件（100。50〜200 のあいだ）を `copy --files-from`（`--transfers` は `TRANSFERS`=4）で送り、`check --one-way` で一致したものだけ `storage=onedrive` にしてから手元を削除する（KAB-355）。copy が非ゼロでも、check で一致した分は進める。一致しないファイルと、check 自体が失敗したバッチは索引も手元も変えない。行は消さない（`status` も `rel_path` も残す）。途中で落ちても、同じコマンドの再実行で続きから揃う。移し済みで手元だけ残っているときも、同じバッチにもう一度載せてから消す
- `--dry-run` は対象を出すだけ。`--limit` はこの実行で手を付ける最大件数。何度流しても、移るものは移った状態に揃う
- 毎日 1 回、`deploy/crontab` から `deploy/cron-run.sh` で動く（KAB-353）。同じジョブが既に動いているときは移さず終わる（終了コード 0）。ロックを持たない、先に手で始めたプロセスも対象なので、全件移動の途中で cron が来ても二重には移さない。手では `deploy/cron-run.sh jobs/market/briefing_offsite.py --dry-run` のあと、`--limit` を付けて少しずつ
- 読む側は `app/market/briefing_files.open_briefing`。`storage=onedrive` は rclone で一時ファイルにして読み、`data/briefing` には戻さない。`analyzer/agent_forecast/fetch_docs.py` の説明資料と、下の読み取りジョブは、索引にあればここから取る
- 保存済み判定（`has_saved` / `saved_document_ids`）は `storage` を見ない

決算説明資料の読み取り（`jobs/market/briefing_extract.py`。KAB-349）:

- `briefing_documents` の PDF を 1 ページずつ Claude（Haiku 5.5）に読ませ、ページのタイトル・要約・表（期間の列 × 行。行に内訳の親 `of`）を返させる。Message Batches API（通常の半額。終わるまで最長 24 時間）で出す。規則と試験は `analyzer/deck_reader/`（`results/` に費用・精度の記録）。本番のコードは `app/market/briefing_extract.py`（文字データ・分類・プロンプト deck-v3・照合・検算）と `app/market/briefing_batch.py`（出す・受け取る・書く）
- PDF は `open_briefing` で開く。手元（`storage=local`）は `data/briefing`（`SHARE_ROOT` があればその下）。`storage=onedrive` は rclone で一時ファイルにして読み、`data/briefing` には戻さない
- `submit`: まだ読んでいない資料（`briefing_extracts` に行が無い）を開示の新しい順に。同じ開示番号が複数の出所にあれば tdnet → irbank → homepage の PDF を読む。表題に台本つきの語（ノーツ付・ノート付・スクリプト付・台本付。`is_script_title`）がある資料は読まない（スライドだけの版と同じスライドなので。PDF と索引は残す。台本つき版しか無い会社の分は読まれない）。表紙・中扉・注意事項のページ（文字が少ない・注意事項の語）は出さない。依頼は 1 ページ 1 件（入力が 10 万トークンを超えると単価が上がるので丸ごと 1 件にしない）。画像は 110dpi の PNG、文字データを添える。システムプロンプトはキャッシュに置く。effort は low。`--no-thinking` で thinking を止める（未検証）。1 Batch は 5000 件・150MB まで。出した資料は `briefing_extracts` に status=`submitted`（batch_id、custom_id の頭 `request_key`）。`--redo` は読み取り済みも出し直す。`--dry-run` は依頼の数を数えるだけ
- `collect`: `submitted` の Batch を見て、終わったものだけ書く（終わっていなければ次の実行で）。PDF の文字データを読み直し、数字が文字データにあるか（`verified`）と内訳の合計（`sum_status`）を確かめる。`briefing_pages`（ページごと。Claude に回さないページも kind だけ）と `briefing_facts`（表を 1 セル 1 行）を資料ごとに消して書き直す。英語と日本語を並べた資料の `$ 4.1B / 41億ドル` のように、同じページ・行名・区分・期間・種類で、単位の倍率（億・十億・10億・兆・百万・千・B・M など）を直すと同じ値になる行は 1 行にする（KAB-396）。倍率を除いた単位（円・ドル）が同じものだけ比べ、範囲（`8.7-9.5`）は数字を全部比べる。残すのはそのページで多く使われている単位、同数なら倍率の小さい単位。検算はまとめたあとの行で行う。1 ページでも読めれば `done`、全部だめ・PDF が無ければ `failed`。1 枚のスライドを Claude が複数に分けて `[{...}, {...}]` や `{...}, {...}` で返したときは、表をつないで 1 ページにする（題名は ` / `、要約は空白でつなぐ）。オブジェクトの後ろのそれ以外の余分は捨てるが、`tables` が無いまま閉じていたら括弧の閉じ過ぎとみてそのページを error にする。資料ごとの完了行は読めなかったページ数を `unparsed=` で出し（`errors=` は Slack に page されるので使わない。資料ごと落ちたものは `failed=` で page される）、続けてそのページ番号と理由を 1 行ずつ出す。JSON が読めなかったページは Claude の応答を `briefing_pages.raw_text` にそのまま残す（Batch の結果は 29 日で消えるので、原因はここで見る。API には出さない）。トークンは `briefing_extracts` に（入力はキャッシュの読み書きを含む）
- 照合は「ページのどこかにその数字がある」しか見ない。別の項目・期間に付けた誤りは見つからない（検算と画面の表で補う）。グラフが画像で貼られた資料は文字データに数字が無く `verified` が付かない（OCR の照合は analyzer の PoC だけ）
- 検算の約束（`check_sums`）: 同じ行が 2 つの表に出たら 1 つにして足す。同じ親に別の切り口の内訳が 2 組（部門別と地域別など）あれば 2 組に分けて確かめる（`split`）。率の列・行は見ない。内訳がすべてプラスで合計に届かなければ一部だけの内訳（`partial`。主要製品だけの表など）。括弧の意味は資料で違う（マイナス・前回予想・率）ので値は書き換えず、合わないときだけ括弧をマイナスとみなして試す（`paren`）。許容は一番粗い表示桁の半分 × (内訳の数 + 1)
- `ANTHROPIC_API_KEY` が要る（従量課金）。手での実行は `deploy/README.md`。cron とデプロイでは走らない

## PTS・分足

**PTS ランキング（`pts_snapshots`）。** 株探 `https://kabutan.jp/warning/` の `pts_night_price_increase` / `pts_night_price_decrease` / `pts_night_volume_ranking`（夜間 PTS。株価は 15 分ディレイ）。取得は `app/market/pts.py`、ジョブは `jobs/market/pts_snapshot.py`。キー不要。画面は `docs/screen/pts.md`。

- 1 ページ 15 銘柄。各ランキング 5 ページ（75 銘柄）まで。リクエストの間は 3 秒
- 1 ページ目の見出し「通常取引 D日終値」の D を、今日以前で一番近いその日にして `session_date` にする。夕方の開始前や休日は前のセッションのまま出るため。前夜の最終（株探のナイトタイムは 06:00 まで）は翌日 15:30 の回が拾う
- 1 ページ目の「HH:MM現在」が同じセッション・ランキングで保存済みなら、残りのページを取らずに飛ばす
- 3 本とも取得に失敗したら非ゼロ終了。cron は平日 15:30〜24:00 の 30 分ごと（`deploy/crontab`）
- 翌営業日の始値は `daily_prices` の `session_date` より後の最初の日足の `open`（調整前）。19:30 の日次バッチの後に埋まる

**分足（`data/intraday/1m/`）。** yfinance の 1 分足（KAB-208）。DB には入れず、日ごとの Parquet（zstd）を `data/intraday/1m/YYYY/YYYY-MM-DD.parquet` に置く。1 ファイルに内国株の全銘柄、列は `code`（J-Quants 5 桁）/ `ts`（Asia/Tokyo）/ `open` / `high` / `low` / `close` / `volume`。取得と読み出しは `app/market/intraday.py`、ジョブは `jobs/market/intraday_1m.py`。キー不要。今は保存だけで、API・画面・analyzer からは読まない（1 分足の出来高が日足と合わないため。下の注意）。

- Yahoo のシンボルは 5 桁の先頭 4 文字 + `.T`（`72030` → `7203.T`、`130A0` → `130A.T`）。1 分足は 1 回 7 日まで、さかのぼれるのは約 30 日。毎回直近 7 日を取り、まだファイルに無い (銘柄, 日) だけ書く
- 値は調整前。Yahoo は分割があると過去の分足をさかのぼって直すので、一度書いた (銘柄, 日) は上書きしない。その回で一番新しい日は常に書く。それより前の日を埋めるのは、DB の日足がその日まであり、間に `adj_factor ≠ 1` が無く、分足の高値と日足の高値の比が 1.2 以内のときだけ
- 読むときに `daily_prices.adj_factor` を、その日より後の分を掛けて調整する（`load(..., db=...)`）。出来高は割り、ライツイシュー（ExRT=3）の係数は使わない。`app/market/adjust.py` と同じ向き。丸めない
- 1 銘柄ずつ 0.5 秒あけて取る（全銘柄で約 45 分）。429 は 60 秒・300 秒待って取り直し、それでも駄目なら打ち切って残りは次の回。1 分足を配っていない銘柄（応答に `tradingPeriods` が無い）は空として数える
- 取れた銘柄が 0、または失敗が 1 割を超えたら非ゼロ終了。cron は平日 20:30（19:30 の日次バッチの後。当日の AdjFactor が DB にある状態で過去の日を埋める）
- Yahoo の東証 1 分足はザラバの値は証券会社のチャートと合う。最後の足は 15:24 で、15:30 のクロージング・オークションの約定（引け値と大きな出来高）が無い。15:25〜15:29 は注文を受けるだけで約定が無いので、足が無くてよい。寄りの数分（09:00〜09:04）が無い日も多い。出来高は 1 本の足に数百万株の塊が入ったり、寄り・引けの分が抜けたりして、1 日の合計が日足（J-Quants と yfinance の日足は一致）と合わない日が多い（トヨタ 2026-10-19〜27 の 7 日で、合計は日足の 0.65〜2.1 倍。10/26 は 09:00 の 1 本が 2,487 万株で、日足は 1,720 万株）。日足の始値・終値・出来高とは合わない。日足は `daily_prices` を使う
- 約定の無い分は足が無い。同じ足を SQLite（主キー込み）に入れるとファイルの数倍だった

## ホームページ URL・短信の表紙

**ホームページ URL（`homepages`）。** 企業マスタの各社について、会社ホームページの URL を 1 本残す。IR ページとの区別はまだしない。API / SPA / cron はまだ無い。ジョブは `python jobs/market/homepages.py --code 4543 --dry-run`。手入力は `--set 7203 https://…`（`source=manual`。`--refresh` でも上書きしない）。束は `config/market/homepage_manual.json` を `--set-file` で入れる（`--dry-run` 可。本番にはまだ入れない）。`--all` の本番投入は承認後。表は `jobs/migrate.py` が作る。HTTP はしない。

コードは J-Quants の 5 桁普通株。CLI と XBRL / 短信の code は `ordinary_code` で畳む（4 桁は末尾 0、優先株は先頭 4 文字 + 0）。

候補は提出の新しさ優先。同じ日なら source の順。XBRL は code ごとに `submitted_at` 最新 1 件だけ読み、その URL が捨てられれば XBRL 候補なし（古い提出へは下がらない）。有効な短信より XBRL の提出が古ければ短信を採る。再実行は UPSERT。値が同じなら `checked_at` だけ進める。

短信は新しい順に、URL が取れた最初を採る。題名に「訂正」があるもの、変換後 HTML（`bytes`）が 5KB 未満のものは飛ばす。直近の数本から URL が取れない会社は、そこで打ち切らず取れるまで古い短信へさかのぼる（表紙が空の変換や文字の無いパスだけの短信より、URL の書いてある古い短信を採る。KAB-322）。対象は題名に「決算短信」または `FinancialStatements` を含むもの、および `statements.type_of_document` に `FinancialStatements` を含むもの（題名が本文の切れ端でも種類の列で判定）。

スキームの無いドメイン（`www.toray.co.jp`、`wize-inc.jp`）は `https://` を補う。本文中のスキーム無しは『URL』『ホームページ』の近くだけ。XBRL の値はラベルが無くても `normalize_url` が受ける。pdfium が `(cid:N)` にした欧文（`N + 29` が印字 ASCII）は戻してから探す。URL 中の空白は除く（`http:// www.example.co.jp`）。`http://https://` のような二重スキームは内側を残す。末尾ラベルが ICANN の TLD でないホスト（`www.prologis`）は捨て、同じ短信の次の URL、無ければより古い短信へ進む。`.toyota` などブランド gTLD は残す。

`jpx.co.jp` は捨てる。例外は日本取引所グループ（86970）自身。持株会社化で書類が旧コードに残る会社は `PREDECESSOR_CODES`（641A0←72400、642A0←95080、590A0←44490）。新しいコードに書類が無いときだけ旧コードを見る。

| source | 出どころ | origin | 備考 |
|---|---|---|---|
| `manual` | `--set` / `--set-file` の手入力 | `manual` | 最上位。自動更新では触らない |
| `xbrl_url` | 最新 1 件の `tse-ed-t:URL` / `tse-re-t:URL` | `xbrl:{doc_id}` | スキーム無し（www. 無しも）と全角コロンを直す |
| `tanshin_html` | 上の条件の決算短信 `tdnet_pdf_documents.html_gz` | `tanshin:{disclosure_no}` | 『URL』『ホームページ』ラベル近くの URL を優先。監査法人・親会社の URL を先頭だけ見ると取り違える。w3.org / TDnet / EDINET / JPX / SEC / SNS はホストの完全一致かドット区切りの末尾一致で捨てる（86970 の `jpx.co.jp` は残す） |

将来の IR / 月次ページは `homepages` に足さず、別表（`code`, `kind`, `url` の複数行）にする。月次ページ URL の正本は `config/monthly/ir_monthly.json` の `index_url`。KAB-320 で `homepages.host` と突き合わせる。上場廃止会社の行は残す（stocks も消さない）。

確度は source で一意なので列にしない（manual > xbrl_url > tanshin_html）。`--all` は今の stocks だけ対象。

ホスト一致の確認は `python jobs/market/homepages.py --all --compare-hosts`（書き込みしない）。

**短信表紙の説明資料（`tanshin_covers`）。** 決算短信1ページ目の「決算補足説明資料作成の有無」「決算説明会開催の有無」を、短信ごとに1行残す（KAB-325）。主キーは `disclosure_no`。`code` にインデックスを付ける。最新の1件は `disclosed_at` が新しい行。API / SPA はまだ無い。表は `jobs/migrate.py` が作る。ジョブは `python jobs/market/tanshin_covers.py --code 7203`。`--limit` は DB に既にある短信から新しい順にその社数だけ、`--days N` は今日（JST）を含む直近 N 日に決算短信を出した会社について、各社の最新1件を書く（`--code` / `--limit` / `--days` はどれか 1 つ）。前の短信の行は消さない。`--all` は無い。日次 cron は 23:35 に `--days 3`（XBRL の最後の回のあと。KAB-416）。毎時の短信の取り込みのあとにも `--days 1`（KAB-438。会社ページの説明資料の `--pending` が読む）。初回は `--days` を 1 四半期より長くして手で一度（`deploy/README.md`）。説明資料 PDF の保存は `jobs/market/tdnet_briefing_r2.py`（KAB-327、`briefing_documents`）。有無の判定とホームページ探索は、その索引を見て別 PR で足す。

- 対象は普通株（`stocks.product_category` が内国株券 `011` または外国株券 `021`）と REIT（`013`）。ETF（`014`）と外国 ETF / ETN（`023`）は除く。REIT でも名称に「ファンド」または「インフラ」がある投資法人は除く。マスタが無い銘柄は、題名が ETF・ETN・投資信託・ファンドでなければ残す
- 題名に「決算短信」があるもの。`訂正` と「決算短信…の修正について／の一部修正」は飛ばす
- 書くのは各社の最新の提出日だけ。PDF（`tdnet_pdf_documents`）は変換 HTML の1ページ目。XBRL はサマリー iXBRL（表紙に相当）。その日に両方あるときは、有無が読めたほうを使い、どちらも読めたときは時刻が新しいほう。同じ日のもう一方の `disclosure_no` は残さない
- 有は 1、無は 0、表紙に文言が無ければ NULL。画像だけの表紙も NULL。説明会の括弧は、投資家・アナリストなど対象が読めるときだけ `briefing_note`。単位の注記や、列の並びに紛れた「氏名」は残さない
- 「有・無」と両方印字されてどちらとも取れないときは NULL（丸印の位置までは見ない）
