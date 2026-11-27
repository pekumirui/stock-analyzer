# MCP のツール

画面を持たない。`mcp/*.py` のモジュール直下の `TOOLS`（名前、説明、引数の schema、関数）を AST で読む。`mcp` は import しない。

- 作り直しは `python -m tools.mcp.render`
- 読み書きする表は出さない。dataflow の観測は API とジョブだけで、MCP は HTTP と生の SQL が混ざる。`db_query` は渡された SELECT 次第で表が変わる
- 引数は schema の `properties` の直下だけ。入れ子のフィールドは引数の説明に残る
- 説明の `{式}` はソースの f-string で、実行時に埋まる部分

## 一覧

| ツール | 登録 | 関数 | 必須の引数 |
|---|---|---|---|
| `fs_list` | `mcp/log_mcp.py` | `fs_tools.tool_fs_list` | `root` |
| `fs_read` | `mcp/log_mcp.py` | `fs_tools.tool_fs_read` | `root`, `path` |
| `fs_grep` | `mcp/log_mcp.py` | `fs_tools.tool_fs_grep` | `root`, `path`, `pattern` |
| `service_status` | `mcp/log_mcp.py` | `tool_service_status` |  |
| `app_logs` | `mcp/log_mcp.py` | `tool_app_logs` |  |
| `batch_logs` | `mcp/log_mcp.py` | `tool_batch_logs` |  |
| `nginx_logs` | `mcp/log_mcp.py` | `tool_nginx_logs` |  |
| `db_stats` | `mcp/log_mcp.py` | `tool_db_stats` |  |
| `db_lookup` | `mcp/log_mcp.py` | `tool_db_lookup` | `code` |
| `db_query` | `mcp/log_mcp.py` | `tool_db_query` | `sql` |
| `db_schema` | `mcp/log_mcp.py` | `tool_db_schema` |  |
| `xbrl_compare` | `mcp/log_mcp.py` | `tool_xbrl_compare` | `code` |
| `financial_statements` | `mcp/log_mcp.py` | `tool_financial_statements` | `code` |
| `xbrl_diff_summary` | `mcp/log_mcp.py` | `tool_xbrl_diff_summary` |  |
| `earnings_day` | `mcp/log_mcp.py` | `tool_earnings_day` |  |
| `earnings_detail` | `mcp/log_mcp.py` | `tool_earnings_detail` | `code` |
| `earnings_docs` | `mcp/log_mcp.py` | `tool_earnings_docs` | `code` |
| `earnings_doc_read` | `mcp/log_mcp.py` | `tool_earnings_doc_read` | `code`, `kind`, `id` |
| `stock_overview` | `mcp/log_mcp.py` | `tool_stock_overview` | `code` |
| `market_context` | `mcp/log_mcp.py` | `tool_market_context` |  |
| `metrics_query` | `mcp/log_mcp.py` | `tool_metrics_query` |  |
| `fx_rate` | `mcp/log_mcp.py` | `tool_fx_rate` | `date` |
| `fx_rates` | `mcp/log_mcp.py` | `tool_fx_rates` | `from`, `to` |
| `monthly_list` | `mcp/log_mcp.py` | `tool_monthly_list` |  |
| `monthly_get` | `mcp/log_mcp.py` | `tool_monthly_get` | `code`, `year` |
| `monthly_add_element` | `mcp/log_mcp.py` | `tool_monthly_add_element` | `code`, `year`, `element`, `unit` |
| `monthly_put_values` | `mcp/log_mcp.py` | `tool_monthly_put_values` | `code`, `year`, `element`, `values` |
| `monthly_delete_element` | `mcp/log_mcp.py` | `tool_monthly_delete_element` | `code`, `year`, `element` |
| `monthly_get_doc` | `mcp/log_mcp.py` | `tool_monthly_get_doc` | `code`, `year` |
| `monthly_put_doc` | `mcp/log_mcp.py` | `tool_monthly_put_doc` | `code`, `year`, `version`, `elements` |
| `monthly_set_industry` | `mcp/log_mcp.py` | `tool_monthly_set_industry` | `code`, `industry` |
| `monthly_ingest` | `mcp/log_mcp.py` | `tool_monthly_ingest` | `code`, `months`, `rows`, `as_of` |

## fs_list

`mcp/log_mcp.py` `fs_tools.tool_fs_list`

許可 root 内のファイル/ディレクトリ一覧（name / path / type / size / mtime）。root は data（リポジトリ data/、MCP_DATA_DIR）か logs（/var/log/app、MCP_LOG_DIR）。path は root からの相対。realpath で正規化し、外へ出る .. / 絶対パス / symlink は拒否。.env* / *.db / *.sqlite* / 鍵類 / rclone.conf / /home/stock/backups / r2-share は出さない。読み取り専用。limit は 1-2000（既定 200）。recursive=true で配下も列挙。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `root` | はい | data / logs | data = repo data/, logs = /var/log/app |
| `path` |  | string | relative path under root; omit or empty for the root itself |
| `recursive` |  | boolean | list descendants; default false |
| `limit` |  | integer | max entries, 1-2000, default 200 |

## fs_read

`mcp/log_mcp.py` `fs_tools.tool_fs_read`

許可 root 内のファイルを部分読み。root / path の約束は fs_list と同じ。1 回の上限は MCP_FS_MAX_BYTES（既定 1MiB）。size はファイル全体、truncated で切り詰めを返す。encoding は utf-8（既定、不正バイトは置換）か base64。読み取り専用。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `root` | はい | data / logs | data = repo data/, logs = /var/log/app |
| `path` | はい | string | relative file path under root |
| `offset` |  | integer | byte offset, default 0 |
| `length` |  | integer | max bytes to return, default and cap ~1MiB |
| `encoding` |  | utf-8 / base64 | utf-8 (default) or base64 |

## fs_grep

`mcp/log_mcp.py` `fs_tools.tool_fs_grep`

許可 root 内のテキストを行grep。大きいログ向け。root / path の約束は fs_list と同じ。path がディレクトリなら配下の通常ファイルを読む。pattern は 1-120 文字。max_matches は 1-500（既定 50）。超えたら truncated。読み取り専用。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `root` | はい | data / logs | data = repo data/, logs = /var/log/app |
| `path` | はい | string | relative file or directory under root |
| `pattern` | はい | string | regex or substring, max 120 chars |
| `max_matches` |  | integer | 1-500, default 50 |

## service_status

`mcp/log_mcp.py` `tool_service_status`

nginx / stock-analyzer / MCP / 監視（monitoring-*）の active 状態、待ち受けポート、localhost:80 / :8000 の HTTP コード。MCP 自身はこの応答自体を ok とし、自分へは curl しない。

引数: なし

## app_logs

`mcp/log_mcp.py` `tool_app_logs`

uvicorn (stock-analyzer) の /var/log/app/stock-analyzer.log と journalctl。since / until 省略時は journal が直近 1h、ファイルは live の末尾だけ。どちらかを指定すると .1 と .gz も読み、行頭の JST YYYY-MM-DD HH:MM:SS で絞る（時刻の無い行は直前の行に付く）。until はその時刻を含まない。昨日は since=yesterday と until=today。lines は 1-500。超えた分は末尾だけ返す。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `since` |  | string | 1h, 30m, today, yesterday, or YYYY-MM-DD. Inclusive. |
| `until` |  | string | Same forms as since. Exclusive. Yesterday is since=yesterday until=today. |
| `grep` |  | string | regex or substring, max 120 chars |
| `lines` |  | integer | 1-500, default 200 |

## batch_logs

`mcp/log_mcp.py` `tool_batch_logs`

cron の /var/log/app/<ジョブ>.log。job は crontab / cron-run.sh のスクリプト名（{', '.join(sorted(BATCH_JOBS))}）。一覧に無い名前とパスは拒否。省略時は stock-analyzer.log と mcp.log 以外の live ファイル（古い batch.log を含む）。since / until は 1h / 30m / today / yesterday / YYYY-MM-DD。until はその時刻を含まない。昨日は since=yesterday と until=today。指定時は .1 と .gz も読み、行頭の JST YYYY-MM-DD HH:MM:SS で絞る。lines は 1-500。超えた分は末尾だけ返すので since / until を狭める。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `job` |  | string | crontab / cron-run.sh script stem without .py, e.g. xbrl_parse |
| `since` |  | string | 1h, 30m, today, yesterday, or YYYY-MM-DD. Inclusive. |
| `until` |  | string | Same forms as since. Exclusive. Yesterday is since=yesterday until=today. |
| `grep` |  | string | regex or substring, max 120 chars |
| `lines` |  | integer | 1-500, default 200. Narrow since/until past 500. |

## nginx_logs

`mcp/log_mcp.py` `tool_nginx_logs`

nginx の journal・/var/log/nginx/error.log・アクセスログ（rt/urt に処理秒）。遅いリクエストは grep に 'rt=[1-9]' などを渡す。since / until 省略時は journal が直近 1h、ファイルは live の末尾だけ。どちらかを指定すると .1 と .gz も読み、行の時刻（JST）で絞る。until はその時刻を含まない。昨日は since=yesterday と until=today。lines は 1-500。超えた分は末尾だけ返すので範囲を狭める。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `since` |  | string | 1h, 30m, today, yesterday, or YYYY-MM-DD. Inclusive. |
| `until` |  | string | Same forms as since. Exclusive. Yesterday is since=yesterday until=today. |
| `grep` |  | string | regex or substring, max 120 chars |
| `lines` |  | integer | 1-500, default 200. Narrow since/until past 500. |

## db_stats

`mcp/log_mcp.py` `tool_db_stats`

stock.db の件数・日付範囲・デモ混在。取込が想定通りか見る用。読み取り専用。既定は MAX(rowid) の概算（削除があると多め）。exact=true で COUNT(*)。tables で対象を絞る。daily_prices の codes/days は exact のときだけ。1クエリ約2秒・全体約8秒で打ち切り、超えた表は (timeout)。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `exact` |  | boolean | true: COUNT(*) and DISTINCT extras. Default false uses MAX(rowid). |
| `tables` |  | array[string] | table names to include. Omit for all. Also accepts a comma-separated string. |

## db_lookup

`mcp/log_mcp.py` `tool_db_lookup`

1銘柄のマスタ・直近株価・決算・指標。code は 7203 / 72030 / 262A0。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `lines` |  | integer | recent price rows, 1-500, default 8 |

## db_query

`mcp/log_mcp.py` `tool_db_query`

stock.db に SELECT / WITH を 1 文だけ実行。先頭の EXPLAIN / EXPLAIN QUERY PLAN も可。読み取り専用（mode=ro / query_only / authorizer）。INSERT / UPDATE / DELETE / DROP / ATTACH / PRAGMA 書き込み / 複数文 / CTE 内の書き込みは拒否。EXPLAIN の後ろも SELECT / WITH 以外は拒否。limit は 1-2000（既定 200）。超えた行と出力バイトは truncated。{DB_QUERY_TIMEOUT_S:.0f} 秒で打ち切り。params は ? の配列か :name のオブジェクト。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `sql` | はい | string | single SELECT or WITH statement, optional EXPLAIN / EXPLAIN QUERY PLAN prefix |
| `params` |  |  | bound parameters: array for ? placeholders, or object for :name |
| `limit` |  | integer | max rows, 1-2000, default 200 |

## db_schema

`mcp/log_mcp.py` `tool_db_schema`

stock.db のテーブル/ビューと列（sqlite_master と pragma_table_info）。読み取り専用。sqlite_* と秘密表は出さない。tables で対象を絞る。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `tables` |  | array[string] | table names to include. Omit for all. Also accepts a comma-separated string. |

## xbrl_compare

`mcp/log_mcp.py` `tool_xbrl_compare`

1銘柄の実績を期末ごとに statements / TDnet XBRL / EDINET XBRL で並べて OK / DIFF / MISSING。既定は直近 8 期。各期に doc_id・提出日・連結/単体・訂正。読み取り専用。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `period_end` |  | string | YYYY-MM-DD; omit for the latest 8 |
| `items` |  | array[string] | std codes (operating_income) or statements columns (operating_profit) |

## financial_statements

`mcp/log_mcp.py` `tool_financial_statements`

1銘柄の財務3表（BS / PL / CF）を科目が行・決算期が列の TSV で。百万円（1株当たりは円）、列は古い→新しい（YY.MM）。PL・CF は期首からの累計。2 列目は XBRL の要素名（ext: は会社独自、pdf: は短信 PDF にしかない行）。期ごとに 有報 > 短信 XBRL > 短信 PDF の順で採り、無い科目は下位で補う（BS は主の表示リンクに載っている科目だけ）。PDF だけの期は、同じ会社で PDF と XBRL がそろった回の対応（値と印字順で突き合わせ）を近い回から当て、当たらない行は同じ表の XBRL のラベルが 1 要素だけ同じなら当てる。見出しが変わる所に `# 見出し > 見出し …` の 1 行（外側から。段の数は書類の表示リンクの宣言どおりで決まっていない。宣言の無い古い書類だけの行は BS が 大区分 > 区分、CF が 営業・投資・財務。見出しの外は `# -`）。金額のある科目の下の内訳（銀行の 有形固定資産 > 建物 など）は、科目名の頭に全角の空白を段の数だけ付ける。最後の行が出どころ。会計基準が 2 つ以上の表は、その前に列ごとの会計基準（JPGAAP / IFRS …）の行。XBRL のタグが替わって割れた同じ科目は 1 行（2 列目は新しい期の要素）。日本基準から IFRS に移った会社は基準ごとに表を分け、新しい基準の表の後ろに `## 前の会計基準の期 …` の行と前の基準の表。読み取り専用。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `statement` |  | earnings_tools.FS_STATEMENTS | default BS |
| `periods` |  | integer | newest periods, 1-{earnings_tools.FS_MAX_PERIODS}, default {earnings_tools.FS_DEFAULT_PERIODS} |
| `consolidated` |  | boolean | false = 個別（単体）。default true |

## xbrl_diff_summary

`mcp/log_mcp.py` `tool_xbrl_diff_summary`

市場全体で XBRL と statements の実績を期末で突き合わせた一致率と DIFF 上位。source は edinet（既定）か tdnet。since は xbrl_filings.submitted_at。restated は TDnet の訂正短信が statements より後に別の開示で出し直した期（一致率に数えない）。読み取り専用。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `source` |  | xbrl_compare.SUMMARY_SOURCES |  |
| `since` |  | string | YYYY-MM-DD lower bound on submitted_at |
| `limit` |  | integer | top DIFF rows, 1-500, default 20 |

## earnings_day

`mcp/log_mcp.py` `tool_earnings_day`

決算速報の 1 日分（決算速報画面と同じ計算）。開示 1 件 1 行と未発表の銘柄を TSV で。列は業種・発表時刻・Q・通期営利予想の上方/下方・訂正・PTS 終値比（株探 PTS ランキング各75位以内だけ）・終値・時価総額・予想PER・売上/営利/経利/純利の累計と YoY・3か月と QoQ（百万円）・営利進捗率と前年同期・決算翌日の騰落。集中日は 700 行を超えるので offset / limit で読む。date 省略は今日。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `date` |  | string | YYYY-MM-DD. Default today |
| `today` |  | string | YYYY-MM-DD the caller treats as today (splits disclosed / not yet). Default server today |
| `sort` |  | earnings_tools.SORTS | time = screen order (default). pts_* by PTS change, rows without PTS last |
| `sectors` |  | array[string] | 33-sector names as in the rows (e.g. 電気機器). 未分類 for none |
| `quarters` |  | array[earnings_tools.QUARTERS] | FY and 5Q count as 4Q |
| `codes` |  | array[string] | only these codes (4 or 5 chars) |
| `offset` |  | integer | rows to skip, default 0 |
| `limit` |  | integer | rows, 1-{earnings_tools.MAX_DAY_LIMIT}, default {earnings_tools.DEFAULT_DAY_LIMIT} |

## earnings_detail

`mcp/log_mcp.py` `tool_earnings_detail`

1 銘柄の決算表（決算速報の業績と同じ）。累計の年比較と中間・通期予想、3か月の時系列、進捗率の年比較、通期予想の修正履歴（直近 4 年度）。disclosure_no 省略は最新の実績開示。金額は百万円。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `disclosure_no` |  | string | disclosure_no column of earnings_day. Omit for the latest actual disclosure |

## earnings_docs

`mcp/log_mcp.py` `tool_earnings_docs`

1 銘柄の短信・開示（TDnet の XBRL 原文と IR BANK の PDF）と決算説明資料の一覧。本文は earnings_doc_read。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |

## earnings_doc_read

`mcp/log_mcp.py` `tool_earnings_doc_read`

earnings_docs の 1 件の本文をテキストで。kind=filing は短信の原文（id と seq）、pdf は PDF から変換した原文（id=開示番号。崩れることがある）、deck は決算説明資料の読み取り結果（id=key。ページごとのタイトル・要約・表）。長いので offset / length で読む。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `kind` | はい | filing / pdf / deck |  |
| `id` | はい | string | filing id / disclosure_no / deck key from earnings_docs |
| `seq` |  | integer | filing document seq from earnings_docs (kind=filing) |
| `offset` |  | integer | character offset, default 0 |
| `length` |  | integer | characters, 1000-{earnings_tools.MAX_DOC_LENGTH}, default {earnings_tools.DEFAULT_DOC_LENGTH} |

## stock_overview

`mcp/log_mcp.py` `tool_stock_overview`

1 銘柄のバリュエーションと株価の位置。予想PER・PBR・予想配当利回り・ROE・自己資本比率・時価総額、次の決算予定、today 時点の騰落（5〜240日）と 1 年高値・安値からの距離、出来高、過去の決算後の株価（翌日〜60日、対TOPIX）。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `today` |  | string | YYYY-MM-DD as-of for price stats and next earnings. Default today |

## market_context

`mcp/log_mcp.py` `tool_market_context`

TOPIX の date 時点の騰落（1〜240日）と 1 年高値からの距離、直近 days 日の終値。date 省略は今日。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `date` |  | string | YYYY-MM-DD. Default today |
| `days` |  | integer | daily rows, 1-250, default 20 |

## metrics_query

`mcp/log_mcp.py` `tool_metrics_query`

VPS のメトリクス（VictoriaMetrics、127.0.0.1 だけ。読み取り専用、保持 6 か月）を PromQL / MetricsQL で引く。start が無ければ instant（end はその時刻）、あれば range（end 省略は今。step 省略は自動）。時刻は 1h, 30m, today, yesterday, YYYY-MM-DD[ HH:MM[:SS]]（JST）。結果の時刻も JST。list_metrics=true でメトリクス名の一覧（grep で絞る）。主な名前: host:cpu_usage:percent / host:memory_usage:percent / host:disk_usage:percent（mountpoint）、node_*（node_exporter）、namedprocess_namegroup_*（groupname=app / mcp / job:<ジョブ名> / nginx など）、cron_job_duration_seconds / cron_job_exit_code / cron_job_last_run_timestamp_seconds（cron_job=<ジョブ名>。ジョブの終わりに 1 点だけ入るので last_over_time(...[1d]) で引く）、ALERTS（vmalert の発火中アラート）。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `query` |  | string | PromQL / MetricsQL expression |
| `start` |  | string | range start: 1h, 30m, today, yesterday, or YYYY-MM-DD[ HH:MM] |
| `end` |  | string | range end or instant time. Same forms. Default now |
| `step` |  | string | range step like 30s, 5m, 1h. Default: auto (max 1100 points) |
| `list_metrics` |  | boolean | list metric names instead of querying |
| `grep` |  | string | with list_metrics: regex or substring, max 120 chars |

## fx_rate

`mcp/log_mcp.py` `tool_fx_rate`

指定日の為替レート。保存は EUR ベース（ECB / Frankfurter）。任意ペアは EUR クロス（例: USD/JPY = EURJPY / EURUSD）。休日・ECB非公表日は指定日以前の直近営業日（as_of）を使う。requested_date と as_of が違うときは holiday_fallback=true。pair（USD/JPY）または base と quote。読み取り専用。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `date` | はい | string | YYYY-MM-DD. Weekends/holidays use the previous ECB business day. |
| `pair` |  | string | USD/JPY. Alternative to base+quote. |
| `base` |  | string | ISO 4217, e.g. USD. Ignored if pair is set. |
| `quote` |  | string | ISO 4217, e.g. JPY. Ignored if pair is set. |

## fx_rates

`mcp/log_mcp.py` `tool_fx_rates`

期間の為替レート（公表日のみ）を TSV で。先頭に始め・終わり・変化率・高値・安値。保存は EUR ベースのクロス計算。休日は系列に含めない。特定日を直近営業日で埋めたいときは fx_rate。pair（USD/JPY）または base と quote。interval は day / week / month（週・月の最後の公表日）。lines は 1-500。読み取り専用。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `from` | はい | string | YYYY-MM-DD inclusive |
| `to` | はい | string | YYYY-MM-DD inclusive |
| `pair` |  | string | USD/JPY. Alternative to base+quote. |
| `base` |  | string | ISO 4217, e.g. USD. Ignored if pair is set. |
| `quote` |  | string | ISO 4217, e.g. JPY. Ignored if pair is set. |
| `interval` |  | day / week / month | default day |
| `lines` |  | integer | max rows, 1-500, default 500 |

## monthly_list

`mcp/log_mcp.py` `tool_monthly_list`

月次表がある銘柄一覧。localhost:8000 の GET /api/v1/tables（nginx は通らない）。

引数: なし

## monthly_get

`mcp/log_mcp.py` `tool_monthly_get`

銘柄×年度の月次表。localhost:8000 の GET /api/v1/tables/{code}/{year}。values は 1〜12。無い月は null。0 は実ゼロ。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `year` | はい | integer | 4-digit calendar year |

## monthly_add_element

`mcp/log_mcp.py` `tool_monthly_add_element`

月次表に要素を登録。localhost:8000 の POST /api/v1/tables/{code}/{year}/elements。unit は PDF 見出しから (number / yoy_index / yoy_pct)。yoy_pct の POST/PUT は保存 unit を yoy_index にする。類似は 409。force=true で強制。既存行の unit 変更は同じパスへ PUT {element, unit}。PUT unit は値を書き換えない。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `year` | はい | integer | 4-digit calendar year |
| `element` | はい | string | row label, 1-50 chars |
| `unit` | はい | number / yoy_index / yoy_pct | From PDF heading, never from magnitude. number=count. yoy_index=100 is flat, store as-is. yoy_pct=signed change (e.g. -7.1); POST stores yoy_index. Value +100 is ingest, not this tool. |
| `force` |  | boolean | create even if a similar element exists |

## monthly_put_values

`mcp/log_mcp.py` `tool_monthly_put_values`

月次セルを一括 upsert（訂正用）。localhost:8000 の PUT /api/v1/tables/{code}/{year}/values/batch。保存形（指数）を送る。unit は無い。+100 しない。value の null は削除。新規登録は monthly_ingest。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `year` | はい | integer | 4-digit calendar year |
| `element` | はい | string | existing row label |
| `values` | はい | array[object] | list of {month, value}; value null deletes the cell |

## monthly_delete_element

`mcp/log_mcp.py` `tool_monthly_delete_element`

月次の空行を消す。localhost:8000 の DELETE /api/v1/tables/{code}/{year}/elements。値が残っていれば 409。表は残す。新規登録は monthly_ingest。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `year` | はい | integer | 4-digit calendar year |
| `element` | はい | string | existing row label |

## monthly_get_doc

`mcp/log_mcp.py` `tool_monthly_get_doc`

銘柄×年度の月次表を 1 枚の文書で返す。localhost:8000 の GET /api/v1/tables/{code}/{year}/doc。空の行も含む。行ごとに element / unit（保存形）/ values（"1"〜"12"）/ tree（木の位置）。version は monthly_put_doc にそのまま渡す。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `year` | はい | integer | 4-digit calendar year |

## monthly_put_doc

`mcp/log_mcp.py` `tool_monthly_put_doc`

monthly_get_doc の文書を直して丸ごと返す。localhost:8000 の PUT /api/v1/tables/{code}/{year}/doc。1 トランザクション。行名は渡したまま（正規化・エイリアスを掛けない）。行の改名は was に元の名前、element に新しい名前（値はサーバーが移す）。文書に無い行は削除。was の無い新しい名前は作成（unit 必須）。values を省いた行は値をそのまま、渡した行は 12 か月を丸ごと置き換え（無い月・null は削除）。values は保存形（+100 しない）。version が GET 後に変わっていれば 409 VERSION_MISMATCH（GET からやり直す）。改名・作成・削除・unit 変更は allow_structure_change=true のときだけ（無ければ 409 STRUCTURE_CHANGED）。dry_run=true は書かずに差分（changes）を返す。木に入らない行は warnings。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `year` | はい | integer | 4-digit calendar year |
| `version` | はい | string | version from monthly_get_doc |
| `elements` | はい | array[object] | all rows of the table after the edit; rows left out are deleted |
| `allow_structure_change` |  | boolean | allow rename / create / delete / unit change |
| `dry_run` |  | boolean | return changes without writing |

## monthly_set_industry

`mcp/log_mcp.py` `tool_monthly_set_industry`

月次業種を付ける。localhost:8000 の PUT /api/v1/tables/{code}/industry。銘柄マスタの業種とは別。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `industry` | はい | string | 月次業種, 1-50 chars. Default その他 until set. |

## monthly_ingest

`mcp/log_mcp.py` `tool_monthly_ingest`

PDF月次を暦年に割り当てて登録。localhost:8000 の POST /api/v1/tables/{code}/ingest。year は渡さない（決算の事業年度末から決める）。dry_run=true は fy_end_month / layout / mapping と書くはずのセルを返し、DB には書かない。only_months は YYYY-MM で、mapping のあと該当列だけ書く。年付き put は訂正用で unit も +100 もしない。unit は PDF 見出しから number / yoy_index / yoy_pct（大きさから推測しない）。yoy_pct は符号付き変化率。ingest は yoy_index と value+100 で書く。yoy_index と number はそのまま。新規行で、strip 後の名前が同じ number と yoy の既存行が rows に無いときは 409 YOY_NUMBER_CONFLICT。同じ POST に両方あれば通る。force=true で作る。

| 引数 | 必須 | 型 | 説明 |
|---|---|---|---|
| `code` | はい | string | 4-5 character stock code (7203, 72030, 262A0) |
| `months` | はい | array[integer] | PDF column months in left-to-right order, integers 1-12. Duplicates allowed for year-wrap (e.g. Genky 13-col Jun…Jun). |
| `rows` | はい | array[object] | list of {element, values, unit, force?}. yoy_index and number store as-is. yoy_pct input is signed; ingest writes yoy_index and value+100. |
| `as_of` | はい | string | YYYY-MM-DD used only to map columns onto calendar years. Not stored as the publication date. |
| `dry_run` |  | boolean | true: return fy_end_month, layout, mapping, and cells that would be written. Do not write the DB. |
| `only_months` |  | array[string] | Calendar YYYY-MM. After mapping, write only those columns. |
| `publication` |  | object | Publication of the newly added month. Omit to store unknown/unspecified. as_of is not copied. |
