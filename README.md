# 株式分析ツール

日本株の株価・決算データを J-Quants API から日次で取り込み、Web/スマホから閲覧する個人用ツール。
設計の詳細は [DESIGN.md](DESIGN.md) 参照。

## 機能

1. **決算速報サマリ.** 日付を選ぶとその日の開示一覧（累計/Q単独、YoY、半期・通期予想、上方修正バッジ）
2. **チャート.** 日足/週足/月足、移動平均、対TOPIX、決算発表日マーカー（既定オフ）
3. **決算発表後リターン.** +1d/+5d/+20d/+60d/現在までの対TOPIX超過リターン (α)
4. **ミネルヴィニ・スクリーナー.** Stage1〜4 と RS ランク (1-99) でフィルタ

## 免責事項

本ツールは個人の学習・分析用です。表示される数値・判定はデータソースの仕様や
本ツールの実装に起因する誤りを含む可能性があります。投資判断は自己責任で行ってください。
本ツールの利用によって生じたいかなる損害についても作者は責任を負いません。

株価・財務データは [J-Quants API](https://jpx-jquants.com/) (JPX総研) を利用しています。
データの再配布は行っていません（本リポジトリにデータファイルは含まれません）。

## クイックスタート (Windows / PowerShell)

APIキーは [jpx-jquants.com](https://jpx-jquants.com/ja) のダッシュボードで発行します。
V2はAPIキー方式（`x-api-key`ヘッダー）で、V1のようなトークン更新は不要です。

```powershell
.\setup.ps1                     # .venv 作成 + 依存インストール
Copy-Item .env.example .env
notepad .env                    # JQUANTS_API_KEY を記入
.\run.ps1 check                 # 疎通確認 + 項目マッピング検証 (DBは触らない)
.\run.ps1 backfill              # 初回: 5年分 (約45分)
.\run.ps1 serve                 # → http://localhost:8000
.\run.ps1 batch                 # 以降の日次 (定常 7 リクエスト。発表予定の台帳が空の初回だけ長い)
```

スクリプト実行がブロックされる場合は一度だけ:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### コマンド一覧

| コマンド | 内容 |
|---|---|
| `.\run.ps1 serve` / `./run.sh serve` / `serve 8080` | サーバー起動 (ポート指定可) |
| `.\run.ps1 check` / `./run.sh check` / `check 2026-08-14` | API疎通・項目マッピング確認 |
| `.\run.ps1 reset` / `./run.sh reset` | DB削除。非対話は `./run.sh reset y` |
| `.\run.ps1 batch` / `./run.sh batch` / `batch 2026-08-14` | 日次取込 (日付指定可) |
| `.\run.ps1 backfill` / `./run.sh backfill` / `backfill 2` | バックフィル (既定5年、年数指定可) |
| `.\run.ps1 lint` / `./run.sh lint` | Ruff + Bandit（`requirements-dev.txt`） |
| `python jobs/fx_rates.py --backfill` | 為替（ECB/EUR）を 1999-01-04 から取得。以後は引数なしで差分 |

> **バックフィル年数の目安**
> 指標の計算にはRSが252営業日、MA200が200営業日の履歴を必要とするため、
> 取得期間の最初の約1年分はステージ判定・RSランクが算出されません。
> 「今のスクリーニングができればいい」なら **2年**（約20分）で十分です。
> 過去の決算後パフォーマンスを遡って見たい場合は5年推奨。
> なお取得可能な期間はプラン上限（Light=5年 / Standard=10年 / Premium=20年）までです。

### 取得方式について

`date` 指定で全上場銘柄が1リクエストで取れるため、**銘柄ループではなく日付ループ**で
取得します（公式ベストプラクティスでも1銘柄ずつの取得は非推奨）。

- 5年バックフィル = 営業日約1,220日 × 2エンドポイント ≒ 2,440リクエスト
- Lightプランは60リクエスト/分 → 約45分
- レート制限は自動でスロットルし、429は指数バックオフで再試行
- 取得済みの日付はスキップするため、中断しても再実行すれば続きから埋まる

> 旧デモ由来の `0` 始まりコードが DB に残っていると RS ランクが狂うため、
> `batch` / `backfill` は実行前に `jobs/market/check_demo.py` で止まります。
> その場合は `.\run.ps1 reset` または `./run.sh reset` してから取り直してください。

決算開示は引け後15時以降が多いため、当日夜(19:30頃)に1回実行する。
タスクスケジューラに登録する場合の操作:

- プログラム: `E:\project\stock-analyzer\.venv\Scripts\python.exe`
- 引数: `jobs\market\run_batch.py`
- 開始場所: `E:\project\stock-analyzer`

（この場合 `.env` は読み込まれないため、認証情報はシステム環境変数に設定するか
`powershell -File run.ps1 batch` で起動する）

## クイックスタート (macOS / Linux)

```bash
./setup.sh                      # .venv 作成 + 依存インストール
cp .env.example .env            # 既存 .env がある場合は上書きしない
# .env に JQUANTS_API_KEY を記入
./run.sh check                  # 疎通確認
./run.sh backfill               # 初回: 5年分 (約45分)
./run.sh serve                  # → http://localhost:8000
./run.sh batch                  # 以降の日次
```

既存の `.env` があるときは `cp .env.example .env` を実行しない。Python は `.venv/bin/python` を使う。
非対話で DB を消すときは `./run.sh reset y`。

## VPS 運用 (ConoHa)

作り直しは不要。同じ FastAPI + SQLite を Ubuntu に置く。推奨はメモリ 2GB / SSD 100GB（東京）。見本は `deploy/`。

初期設定（nginx / ufw / certbot / systemd / crontab）はパイプラインが冪等にやる。root でやるのはデプロイ用 SSH 鍵と sudoers だけ。
手順は [deploy/README.md](deploy/README.md)。

uvicorn はワーカー 1。SQLite は複数ライターに弱い。日次バッチは定常で 7 リクエスト。

```bash
# 開発PC → VPS（backfill 完了後。journal が残っていないことを確認）
scp stock.db user@YOUR_HOST:/home/user/stock-analyzer/stock.db
```

DB のバックアップは ConoHa の自動バックアップか、日次で別パスへコピー。

## CI/CD

`main` にマージすると GitHub Actions がテスト → GitHub Release（タグ）→ ConoHa VPS へデプロイする。

1. **PR.** `tests/ci_smoke.py`（起動・検索/スクリーナー/チャート）と Ruff / Bandit（`app` `jobs` `tests` `tools` `deploy`。`app/xbrlp` は除外）
2. **main マージ.** コミットメッセージでバージョンを自動バンプ（`feat:` は minor、`BREAKING CHANGE` / `feat!:` は major、それ以外は patch）。初回は `v0.1.0`
3. **VPS.** SSH で `git pull` → `provision.sh`（nginx/ufw/certbot/systemd）→ `pip install` → `migrate.py` → `systemctl restart`。
   `stock.db` は触らない

VPS へ自動デプロイするには、一度だけデプロイ用 SSH 鍵を入れる。手順と Secrets 一覧は [deploy/README.md](deploy/README.md)。
`VPS_SSH_KEY` が無い間は Release まで行い、VPS デプロイはスキップする。再デプロイは Actions の **Run workflow**（`bump: none`）。

## 構成

```
app/
  config.py     設定・ステージ判定閾値
  models.py     DBスキーマ (SQLAlchemy)
  market/       株価・決算 (チャート・スクリーナー・決算タブ)
    jquants.py    J-Quants APIクライアント
    ingest.py     取込バッチ (UPSERT・冪等)
    analytics.py  MA/RS/ステージ/Q単独/決算後リターン
  monthly/      月次 (行名の正規化・親子・公開ページ)
  supply/       サプライ (素材マスタ・e-Stat・経産省)
  api/          REST API (機能ごとに 1 ファイル。月次は api/monthly/)
  main.py       FastAPIエントリ
config/         機能ごとの JSON (monthly/ supply/ xbrl/)
static/
  index.html    SPA マークアップ
  css/app.css   スタイル
  js/           タブ別 vanilla JS
jobs/           機能ごと (market/ monthly/ supply/ xbrl/ ops/)。cron は deploy/crontab
  market/run_batch.py   日次バッチ / バックフィル
  market/check_demo.py  スキーマ + 旧デモ取り残しガード
  ops/backup_db.py      本番 SQLite の日次バックアップ（cron は 05:00 JST。手順は docs/DESIGN_backup.md）
  ops/vps_packages_r2.py  VPS パッケージ一覧 → R2（cron は 06:40 JST）
  fx_rates.py           Frankfurter 為替（EUR ベース。cron は 08:00 JST）
  migrate.py            デプロイごとのスキーマ追随と月次の後始末
tests/          機能ごと (market/ monthly/ supply/ xbrl/ ops/)
  ci_smoke.py   CI 起動スモーク (各テストもここから走る)
tools/          機能ごと (market/ monthly/ supply/ xbrl/)
  market/check_api.py  J-Quants 疎通確認
analyzer/       stock.db を読むだけの分析（一覧は analyzer/README.md）
deploy/
  stock-analyzer.service  systemd（uvicorn ワーカー1）
  nginx.conf              リバースプロキシ。月次閲覧は公開、/app は Basic 認証
  crontab                 J-Quants 19:30 JST、為替 08:00 JST、パッケージ一覧 06:40 JST、SQLite バックアップ 05:00 JST
  update.sh               VPS 側デプロイ（Actions から実行）
  enable-cicd.sh          デプロイ用 SSH 鍵と sudoers を一度だけ入れる
```
