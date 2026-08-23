# 株式分析ツール

日本株の株価・決算データを J-Quants API から日次で取り込み、Web/スマホから閲覧する個人用ツール。
設計の詳細は [DESIGN.md](DESIGN.md) 参照。

## 機能

1. **決算速報サマリ** — カレンダーから日付選択 → その日の開示一覧 (累計/Q単独、YoY成長率、半期・通期予想、上方修正バッジ)
2. **チャート** — 日足/月足、移動平均線カスタム、対TOPIX相対パフォーマンス、決算日マーカー
3. **決算発表後リターン** — +1d/+5d/+20d/+60d/現在までの対TOPIX超過リターン(α)
4. **ミネルヴィニ・スクリーナー** — Stage1〜4判定 + RSランク(1-99)でフィルタ

## 免責事項

本ツールは個人の学習・分析用です。表示される数値・判定はデータソースの仕様や
本ツールの実装に起因する誤りを含む可能性があります。投資判断は自己責任で行ってください。
本ツールの利用によって生じたいかなる損害についても作者は責任を負いません。

株価・財務データは [J-Quants API](https://jpx-jquants.com/) (JPX総研) を利用しています。
データの再配布は行っていません（本リポジトリにデータファイルは含まれません）。

## クイックスタート (Windows / PowerShell)

```powershell
.\setup.ps1        # .venv 作成 + 依存インストール
.\run.ps1 seed     # デモデータ投入 (J-Quants不要)
.\run.ps1 serve    # → http://localhost:8000
```

スクリプト実行がブロックされる場合は一度だけ:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

## 実データ運用 (J-Quants API V2 / Light)

APIキーは [jpx-jquants.com](https://jpx-jquants.com/ja) のダッシュボードで発行します。
V2はAPIキー方式（`x-api-key`ヘッダー）で、V1のようなトークン更新は不要です。

```powershell
.\run.ps1 reset                # ★デモデータを消す (本番投入前に必須)
Copy-Item .env.example .env
notepad .env                   # JQUANTS_API_KEY を記入
.\run.ps1 check                # 疎通確認 + 項目マッピング検証 (DBは触らない)
.\run.ps1 backfill             # 初回: 5年分 (約45分)
.\run.ps1 batch                # 日次 (4リクエストで完了)
```

### コマンド一覧

| コマンド | 内容 |
|---|---|
| `.\run.ps1 seed` | デモデータ投入 |
| `.\run.ps1 serve` / `serve 8080` | サーバー起動 (ポート指定可) |
| `.\run.ps1 check` / `check 2026-08-14` | API疎通・項目マッピング確認 |
| `.\run.ps1 reset` | DB削除 |
| `.\run.ps1 batch` / `batch 2026-08-14` | 日次取込 (日付指定可) |
| `.\run.ps1 backfill` / `backfill 2` | バックフィル (既定5年、年数指定可) |

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

> **デモデータと実データは絶対に混ぜないこと。**
> RSランクは全銘柄を横断してパーセンタイルを取るため、架空銘柄が1つでも残ると
> 全銘柄のランクとステージ判定が狂います。
> デモ銘柄コードは `0` 始まり (実コードは1-9始まりなので衝突はしない) にしてあり、
> `batch` / `backfill` は実行前に `scripts/check_demo.py` で自動チェックして止まります。

決算開示は引け後15時以降が多いため、当日夜(19:30頃)と翌朝6時の2回実行を推奨。
タスクスケジューラに登録する場合の操作:

- プログラム: `E:\project\stock-analyzer\.venv\Scripts\python.exe`
- 引数: `scripts\run_batch.py`
- 開始場所: `E:\project\stock-analyzer`

（この場合 `.env` は読み込まれないため、認証情報はシステム環境変数に設定するか
`powershell -File run.ps1 batch` で起動する）

## macOS / Linux の場合

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_demo.py
uvicorn app.main:app --reload
# 実データ: set -a && . ./.env && set +a && python scripts/run_batch.py
```

## VPS 運用 (ConoHa)

作り直しは不要。同じ FastAPI + SQLite を Ubuntu に置く。推奨はメモリ 2GB / SSD 100GB（東京）。見本は `deploy/`。

1. Ubuntu 24.04 を立て、SSH 鍵と ufw（22/80/443）を入れる
2. 専用ユーザでリポジトリを clone し、venv + `pip install -r requirements.txt`
3. `.env` に `JQUANTS_API_KEY` だけ書く（コミットしない）
4. ローカルの backfill が終わってから `stock.db` を scp する。サーバで 5 年分を取り直さない
5. `deploy/stock-analyzer.service` をパス・ユーザに合わせて `/etc/systemd/system/` へ
6. `deploy/nginx.conf` をサイトにコピー。ドメインがあれば certbot、無ければ当面 IP + HTTP
7. Basic 認証（`htpasswd`）。世界公開しない
8. `deploy/crontab` をそのユーザの crontab に入れる（19:30 と 06:00 JST）

uvicorn はワーカー 1。SQLite は複数ライターに弱い。日次バッチは 4 リクエストで短い。

```bash
# 開発PC → VPS（backfill 完了後。journal が残っていないことを確認）
scp stock.db user@YOUR_HOST:/home/user/stock-analyzer/stock.db
```

DB のバックアップは ConoHa の自動バックアップか、日次で別パスへコピー。

## CI/CD

`main` にマージすると GitHub Actions がテスト → GitHub Release（タグ）→ ConoHa VPS へデプロイする。

1. **PR** — `scripts/ci_smoke.py`（起動・デモ投入・検索/スクリーナー/チャート）
2. **main マージ** — コミットメッセージでバージョンを自動バンプ（`feat:` は minor、`BREAKING CHANGE` / `feat!:` は major、それ以外は patch）。初回は `v0.1.0`
3. **VPS** — SSH で `git pull` → `pip install` → `migrate.py` → `systemctl restart`。`stock.db` と `.env` は触らない

VPS へ自動デプロイするには、一度だけデプロイ用 SSH 鍵を入れる:

```bash
ssh-keygen -t ed25519 -C "github-actions@stock-analyzer" -f stock-analyzer-deploy -N ""
# stock-analyzer-deploy の中身 → GitHub → Settings → Secrets and variables → Actions → VPS_SSH_KEY
ssh -t root@YOUR_HOST "cd /home/stock/stock-analyzer && sudo bash deploy/enable-cicd.sh '$(cat stock-analyzer-deploy.pub)'"
```

`VPS_SSH_KEY` が無い間は Release まで行い、VPS デプロイはスキップする。再デプロイは Actions の **Run workflow**（`bump: none`）。詳細は [deploy/README.md](deploy/README.md)。

## 構成

```
app/
  config.py     設定・ステージ判定閾値
  models.py     DBスキーマ (SQLAlchemy)
  jquants.py    J-Quants APIクライアント
  ingest.py     取込バッチ (UPSERT・冪等)
  analytics.py  MA/RS/ステージ/Q単独/決算後リターン
  api.py        REST API
  main.py       FastAPIエントリ
static/index.html   SPA (スマホ対応)
scripts/
  run_batch.py  日次バッチ / バックフィル
  seed_demo.py  デモデータ生成
  ci_smoke.py   CI 起動スモーク
deploy/
  stock-analyzer.service  systemd（uvicorn ワーカー1）
  nginx.conf              リバースプロキシ + Basic認証
  crontab                 19:30 / 06:00 JST
  update.sh               VPS 側デプロイ（Actions から実行）
  enable-cicd.sh          デプロイ用 SSH 鍵と sudoers を一度だけ入れる
```
