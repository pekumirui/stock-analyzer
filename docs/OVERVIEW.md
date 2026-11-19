# 全体像

設計書の入口。画面は [画面設計](SCREENS.md)、テーブルは [データモデル](SCHEMA.md)、バッチは [ジョブ設計](JOBS.md)。

## 構成

コードの依存は [モジュールの依存](overview/modules.md)、`deploy/` から読める範囲の構成（systemd・nginx・cron）は [システム構成](overview/system.md)（どちらも自動生成）。下の図は概念図。

```
┌─────────────┐   日次バッチ(19:30 JST)   ┌──────────────┐
│ J-Quants API │ ─────────────────────→ │  DB           │
│ (Light plan) │   上場銘柄/株価/財務/TOPIX │  SQLite        │
└─────────────┘                          │  (開発PC / VPS) │
┌─────────────┐   補完(必要時)            └──────┬───────┘
│ IRBank MCP  │ ─────────────────────→        │
└─────────────┘                                ↓
                                    ┌──────────────────┐
                                    │ FastAPI (REST API) │
                                    │ + 静的SPA配信       │
                                    └──────┬───────────┘
                                           │ HTTPS
                                 ┌─────────┴─────────┐
                                 │  Web / スマホブラウザ │
                                 └───────────────────┘
```

- **言語**: Python 3.10+ / FastAPI / SQLAlchemy / pandas
- **フロント**: `static/index.html` + `static/css/` + `static/js/` の vanilla JS SPA (lightweight-charts。`static/vendor/` から配る)。スマホ対応
- **バッチ**: `jobs/market/run_batch.py` を cron / スケジューラで日次実行

## J-Quants API

**V2 の Light プラン**を使う。エンドポイント・項目・プラン別の取得期間とレートリミットは J-Quants の MCP で引く。ここには本ツールの選び方だけ書く。

- 取込は**日付ループ**。`date` だけ指定して全上場銘柄のその日を 1 リクエストで取る（銘柄ループはしない）。調整済み株価は API の値をそのまま使う（CSV 一括 + 自前調整はしない）
- `jquants.py` はエンドポイント群ごとにスライディングウィンドウで自主規制する。パスが `/fins/` で始まるものはプランと別枠。429 は指数バックオフ（5→10→20→40→60 秒）で最大 6 回再試行
- RS に要る 252 営業日 + α は Light の取得期間で足りる

## テーブル

テーブル・列・索引・ER 図は [`docs/SCHEMA.md`](SCHEMA.md)（`app/models.py` から自動生成。列の意味もそこに出る）。EDINET の表は [`docs/jobs/edinet.md`](jobs/edinet.md)、XBRL の表は [`docs/jobs/xbrl.md`](jobs/xbrl.md)。ここには取込と保存の規則を書く。

## 分析ロジック

### 4.1 レラティブストレングス (RS)
```
rs_raw = ((C-C63)/C63*0.4 + (C-C126)/C126*0.2 + (C-C189)/C189*0.2 + (C-C252)/C252*0.2) * 100
```
adj_close を使用。`stocks.product_category = 011`(内国株券) の銘柄だけで降順に並べ、上位1%→99、次の1%→98…と1-99を割当。
REIT・ETF・優先出資はユニバースに入れない (ミネルヴィニのRSは普通株同士の相対)。
ProdCat がまだ1件も入っていないDBでは従来どおり全銘柄でランクする (migrate 直後のフォールバック)。
ステージ判定・スクリーナーも同じユニバース。チャート検索では REIT も引ける。

### 4.2 ミネルヴィニ・ステージ判定
優先順位付きルールベース (Stage2テンプレート → Stage4 → Stage3 → Stage1 → 未分類)。
Stage3 を Stage1 より先に見るのは、天井圏で 200日線に沿って横ばいの銘柄を底固めにしないため。
52週高値・安値は調整後終値の 252 営業日の最大・最小。どれにも当たらなければ未分類 (`stage` が NULL) のまま。

**Stage 2 (全条件AND。トレンドテンプレート)**:

- C > MA150 かつ C > MA200
- MA150 > MA200
- MA200 が1ヶ月以上上昇 (直近21営業日すべてで前日より上)
- MA50 > MA150 > MA200
- C > MA50
- C ≥ 52週安値×1.3
- C ≥ 52週高値×0.75
- RSランク ≥ 70

**Stage 4**: C < MA200 かつ MA200下降 (`ma200_slope_1m < -閾値`) かつ Cが52週安値の1.1倍以内

**Stage 3 (天井圏)**: 次の 2 つと、警戒サインのどれか 1 つ

- 直近120営業日以内に Stage2 だった
- MA200 が失速: ほぼ横ばい (|slope| < 閾値)、または上向きでも `ma200_slope_1m` が直近120営業日の最大の半分以下
- 警戒サイン (OR): 値動きが荒くなった (直近20日の ATR/株価 が、その前の20日より大きい。ATR は調整後の高値・安値・前日終値の true range の平均)、または大商いの下げ (直近20日に、前日までの50日平均出来高の1.5倍以上で前日より下げた日がある)

出来高を伴わずに下げ始めた銘柄も値幅で拾う。どちらのサインも無ければ未分類。

**Stage 1**: MA200±8%以内に株価が滞留 (直近60日の帯域内比率が70%以上) かつ MA200がほぼ横ばい (|slope| < 閾値)

閾値は `config.py` に集約し調整可能。

### 4.3 決算日からの相対パフォーマンス
決算発表後に買えた最初の値（調整後）を基準価格にし、各期間の `(株価リターン − TOPIXリターン)` を算出。テーブルで +1d/+5d/+20d/+60d/現在 を表示。買えない値（発表前の始値、張り付いた値）は使わない。

- 大引け（2024-11-05 から 15:30、それまで 15:00）以降の発表は翌営業日の始値
- それより前（場中）の発表は発表当日の終値。発表後の最初の値は Light では取れない。当日がストップ高で引けた（終値が高値で `upper_limit` が 1。NULL の過去の行は前日終値から制限値幅いっぱい上げたか）なら引けでは買えないので翌営業日の始値
- 始値で買う日が寄らずのストップ高・ストップ安なら翌営業日へ送り、寄った日まで続ける。寄らずは四本値が同じで、`upper_limit` / `lower_limit` が 1（NULL の過去の行は前日終値から制限値幅いっぱい動いたか。`app/market/price_limit.py`）。寄ってすぐ張り付いた日も日足では区別できないので寄らずとみなす
- +Nd は始値で買ったなら基準日を、終値で買ったなら翌営業日を 1 日目として N 日目の終値。TOPIX も基準と同じ値（始値か終値）から
- 「次の決算まで」は、同じ銘柄の次の期の最初の決算短信（訂正・予想修正だけは数えない）の発表日の前営業日の終値まで。次の決算がまだなら株価の最終日まで。ランキングタブはこれで並べる
チャート下のイベント重ね合わせ（既定オフ）で決算発表日に「決」マーカーを出せる。今後のイベント種別も同じチェック列に足す。決算速報タブだけ、選択中の発表日は重ね合わせオフでも1件出す。週足・月足では発表日を含む足（期間末）に載せる。

## ホスティング

本番は **ConoHa VPS + 同じディスク上の SQLite**。Neon / Vercel Postgres の無料枠 (0.5GB) には 5 年分日足が乗らない。アプリはサーバレス前提ではないので作り直さない。

| 役割 | 置き場 |
|---|---|
| API + SPA | 同一 VPS の uvicorn（systemd。ワーカー数はコアと MemAvailable、上限 2 本。`UVICORN_WORKERS` で上書き） |
| DB | `stock.db`（VPS の SSD）。`DATABASE_URL` は今の SQLite のまま |
| バッチ | crontab（19:30 JST の J-Quants 日次。株探 PTS は平日 15:30〜24:00 の 30 分ごと。yfinance 1 分足は平日 20:30。為替は 08:00 JST。パッケージ一覧は 06:40 JST。SQLite バックアップは 05:00 JST） |
| バックアップ | OneDrive（日次 7・週次 4）。VPS の gzip は送信確認後に消す。手順は [`docs/DESIGN_backup.md`](DESIGN_backup.md) |
| 公開 | nginx + Let's Encrypt。月次閲覧は `/monthly`（Access なし）。個人ツールは `/app` + Cloudflare Access。`/static/` は nginx が直配信（`monthly_row_order.json` / `ir_monthly_codes.json` だけ uvicorn） |
| 開発 | 今どおり Windows + `.\run.ps1` |

推奨プランは **メモリ 2GB / SSD 100GB**（東京）。512MB は Python + pandas + SQLite で足りない。時間課金で立ててから長期契約を判断する。

データ量は VPS のディスクなら余裕。
ローカル backfill 完了後に `stock.db` を scp する（サーバで取り直さない）。

見本は `deploy/`（systemd / nginx / crontab）。uvicorn のワーカー数は `nproc` と MemAvailable（1 本あたり数百 MiB。`deploy/uvicorn_workers.py`）で決め、2 本で頭打ちにする。SQLite はライター 1 本のまま WAL + `busy_timeout=30s` で待ち合わせる。ファイル SQLite は `NullPool`（SQLAlchemy 2.1 の QueuePool 既定は使わない。接続が増えると locked になる）。日次バッチは定常で 7 リクエスト。指標は差分だけをチャンク commit。月次の書き込み API は `database is locked` を短い回数だけ再試行する。`jobs/migrate.py`（deploy 含む）は同じエラーを指数バックオフで再試行する。起動前に migrate が `init_db` を 1 回だけ流す（`--preload` は使わない）。

Postgres 切替は将来オプション。今は使わない。`DATABASE_URL` だけ変えれば SQLAlchemy は両対応のまま。

## 将来拡張

- **米国株**: データソース差替え(Polygon/Alpaca等)を想定し、`source` 列と市場別カレンダー・通貨を吸収するアダプタ層を`jquants.py`と同一インターフェースで追加
- **IRBank MCP**: J-Quantsに無い項目 (セグメント、株主、信用残日次) の補完
- 通知 (Stage2入り銘柄・上方修正をLINE/メール)
- **銘柄KPI（業種で項目が違う）**: 小売の月次売上・製造の受注残など。J-Quants `/fins/summary` には無い。業種ごとの列を `statements` に足さない。無い銘柄は行を持たない疎な時系列にする

```
stock_kpis  PK(code, metric, period)
  code / metric (monthly_sales, order_backlog, …)
  period (2026-07 / 2026Q1) / value / unit / source / disclosed_date
```

業種テンプレ（`config.py`）が「小売なら monthly_sales を見る」と決める。テンプレにあっても値が無い銘柄は UI で「なし」。決算予測は、その銘柄に実際にある metric だけ使う。取込は後で IR など。テーブルも今は作らない。

## ローカル実行手順

[README.md](../README.md) のクイックスタートとコマンド一覧。

### ゴールデンデータの DB（開発・クラウドのエージェント・テスト）

`./run.sh golden`（`jobs/golden_db.py`）が空の `stock.db` を作り（スキーマは `init_db`）、`tests/golden/db/<table>.csv` を表ごとに入れる。数秒。`.env` は要らない。クラウドのエージェントの `install.sh` は `stock.db` が無ければこれで作る（`STOCK_DB=full` なら代わりに backfill 2）。

- 中身は実データの切り出し。株価・決算・決算予定は `tests/golden/db/codes.txt` の約45銘柄（ステージ2・RS 上位、時価総額上位、REIT 1、月次の主要銘柄）。銘柄マスタ・TOPIX・決算予定の取得記録は全件。月次は本番の全銘柄・全年。為替は株価と同じ期間。素材は e-Stat / 経産省の直近の年。期間と件数は `tests/golden/db/manifest.json`
- `indicators` は持たず、読み込みのあとに `compute_indicators` で計算し直す（RS ランクは約45銘柄の中の順位になる）
- XBRL は持たない（本番 ZIP は `data/xbrl`）。`monthly_element_sources` は読み込みのあとに `legacy` で埋める
- 作った DB は `PRAGMA user_version = 1`。`install.sh` はこれを見て、ゴールデンデータの DB には日次 batch を回さない（全銘柄の当日分が混ざるため）
- 既に行がある DB には入れない。作り直しは `./run.sh reset y` → `./run.sh golden`
- データの作り直しは `python tools/golden/build_golden_db.py --db stock.db --api https://kabukpi.com`（backfill 済みの `stock.db` と月次の公開 GET から。`--skip market|monthly|fx|material` で表の組を飛ばす）。スキーマを変えたら作り直す。`tests/test_golden_db.py` は CSV の列がモデルと違えば落ちる
