# VPS (ConoHa / Ubuntu 24.04)

手で nginx / ufw / systemd を積む必要はない。**root でデプロイ鍵を一度入れる**と、あとは GitHub Actions が冪等に初期設定する。

`deploy/provision.sh` が apt / nginx / ufw / certbot / systemd / crontab を冪等に揃える。

## 一度だけ（root）

```bash
ssh-keygen -t ed25519 -C "github-actions@stock-analyzer" -f stock-analyzer-deploy -N ""
# 秘密鍵の中身 → GitHub → Settings → Secrets and variables → Actions → VPS_SSH_KEY
ssh -t root@YOUR_HOST "bash -s '$(cat stock-analyzer-deploy.pub)'" < deploy/enable-cicd.sh
```

`enable-cicd.sh` がやること（冪等）:

- `stock` ユーザ（無ければ作成）
- GitHub 用 SSH 鍵（無ければ作成）と `origin` の SSH 化
- リポジトリ clone（無ければ。プライベートなので HTTPS は使わない）
- `authorized_keys` にデプロイ公開鍵
- `/etc/sudoers.d/stock-analyzer`（`provision.sh` と `systemctl` だけ）

リポジトリはプライベート。VPS の `stock` ユーザが `git pull` するには GitHub Deploy key（read-only）が必要。
`setup-github-ssh.sh` が鍵を作り公開鍵を出すので、GitHub → Settings → Deploy keys に一度だけ登録する。
clone が鍵未登録で落ちたら、出した公開鍵を入れてから `enable-cicd.sh` を再実行。

sudoers を更新したあとは、既存 VPS でも **もう一度 `enable-cicd.sh` を実行する**。ただし `provision.sh` も sudoers を冪等に入れるので、通常の再デプロイだけでも足りる。

## GitHub Secrets

| Name | 必須 | 内容 |
|---|---|---|
| `VPS_SSH_KEY` | はい | デプロイ用秘密鍵 |
| `JQUANTS_API_KEY` | 推奨 | あるときだけ `.env` の当該行を更新。無ければ既存 `.env` を触らない |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | 推奨 | Cloudflare R2 (bucket `share`) の rclone 認証。あるときだけ `.env` と `~/.config/rclone/rclone.conf` を更新。無ければ既存を触らない |
| `SLACK_WEBHOOK_URL` | 推奨 | Slack Incoming Webhook（#alerts）。コミットしない。漏れたら rotate。provision が `/etc/fluent-bit/slack.env` に URL と `SLACK_WEBHOOK_URI` を書く。`.env` には入れない |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | 推奨 | nginx Basic 認証。無ければ既存 `htpasswd` を残す |
| `MCP_TOKEN` | 推奨 | ログ MCP の Bearer トークン。`openssl rand -hex 32`。無ければ VPS 上で生成（値は CI に出ない） |
| `VPS_DOMAIN` | 任意 | 未設定なら `160.251.179.50.sslip.io` |
| `VPS_HOST` / `VPS_USER` / `VPS_PORT` | 任意 | 未設定なら `160.251.179.50` / `stock` / `22` |
| `CERTBOT_EMAIL` | 任意 | 無いと certbot は `--register-unsafely-without-email` |

`stock.db` はパイプラインが触らない。初回データは手元で backfill して scp するか、VPS 上で `scripts/run_batch.py`。

## パイプラインが毎回やること

`update.sh`:

1. GitHub SSH（deploy key）を冪等に揃えてから `git pull --ff-only origin main`
2. `JQUANTS_API_KEY` / `MCP_TOKEN` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` があれば `.env` のその行だけ upsert。R2 の両方が揃っていれば `~/.config/rclone/rclone.conf` も書く。`SLACK_WEBHOOK_URL` は `.env` に書かない
3. `sudo provision.sh`（パッケージ、systemd、nginx、ufw 22/80/443、Let's Encrypt、crontab、sudoers、R2 user mount、fluent-bit）。Webhook が空なら fluent-bit は enable のみで start しない
4. venv / `pip install` / `migrate.py`
5. `systemctl restart stock-analyzer` と `stock-analyzer-mcp`、localhost:8000 / :8010 のヘルスチェック

証明書の更新は `certbot.timer`（1日2回）。更新後は `renewal-hooks/deploy` で nginx を reload する。

ブラウザは [https://kabukpi.com/](https://kabukpi.com/)（証明書は RSA + ISRG Root X1。ECDSA 既定の Root YE は端末にまだ無く「セキュリティ保護なし」になる）。

ホスト鍵は `deploy/known_hosts` にピン留め。VPS を作り直したら `ssh-keyscan YOUR_HOST` で更新する。

## このPCから SSH（鍵のみ）

sshd はパスワードを受け付けない（`deploy/provision.sh` が `99-key-only.conf` を入れる）。手元の公開鍵は `deploy/admin_keys` に書き、root と `stock` の `authorized_keys` へ冪等に足す。

```
# 手元 ~/.ssh/config の例
Host stock-vps
  HostName 160.251.179.50
  User stock
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes

Host stock-vps-root
  HostName 160.251.179.50
  User root
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
```

`ssh stock-vps` / `ssh stock-vps-root`。GitHub Actions のデプロイ鍵は従来どおり `stock` の `authorized_keys` だけ。

失敗したデプロイのやり直しは Actions の **Run workflow**（`bump: none`）。

## ログ MCP（Grok）

VPS 上で `deploy/log_mcp.py` が `127.0.0.1:8010` を待ち、nginx が Basic 認証なしで `/mcp` だけプロキシする。ポート 8010 は外に開けない。

ツール:

- `service_status` — プロセスと localhost の HTTP コード
- `app_logs` — `/var/log/app/stock-analyzer.log` と journalctl
- `batch_logs` — `/var/log/app/batch.log`
- `nginx_logs` — nginx journal と error.log
- `db_stats` — stock.db の件数・日付範囲・デモ混在
- `db_lookup` — 1銘柄のマスタ・直近株価・決算・指標
- `monthly_list` — 月次表一覧。localhost:8000 の GET /api/v1/tables
- `monthly_get` — 銘柄×年度の月次表。values は 1〜12。無い月は null
- `monthly_add_element` — 要素登録
- `monthly_put_values` — セル一括 upsert（null で削除）。year は暦年。訂正用
- `monthly_delete_element` — 値の無い行を消す。値が残っていれば 409。表は残す
- `monthly_set_industry` — 月次業種。PUT /api/v1/tables/{code}/industry。銘柄マスタの業種とは別
- `monthly_ingest` — PDF 月次の新規登録。POST /api/v1/tables/{code}/ingest。year は渡さない。
  列順 + as_of 時点の最新 `fiscal_year_end` から暦年を決める。月が重複する year-wrap は列順配列の `values`

任意コマンドも restart も任意 SQL もない。月次ツールは FastAPI の `http://127.0.0.1:8000` だけを叩く（nginx Basic は使わない）。行数は最大 500。`.env` の値は伏せる。
`monthly_get` の `values` は 1〜12。無い月は null。0 は実ゼロ。1年通して値のない行は出さない。
月次の unit は PDF 見出しから付ける（`DESIGN.md` §3 の表）。既存行の unit 変更は `PUT /api/v1/tables/{code}/{year}/elements`（`{element, unit}`）。

認証は `MCP_TOKEN`（Bearer または OAuth で発行した access token）。GitHub secret と同値。値をログに出さない。

アプリ・バッチ・MCP のファイルログは `/var/log/app/`（`stock-analyzer.log` / `batch.log` / `mcp.log`）。journal も残る。
14 日で logrotate（copytruncate）。nginx は従来どおり `/var/log/nginx/`。ローカルのバッチはアプリディレクトリの `batch.log`。

fluent-bit は live ファイルだけを tail する（`/var/log/app/stock-analyzer.log` / `batch.log` / `mcp.log`、`/var/log/nginx/error.log`）。systemd は `stock-analyzer.service` と `stock-analyzer-mcp.service` の `Failed with result` / `Main process exited`。access.log と `html_ingest_ledger.json` と rotate 済みの `.1` / `.gz` は見ない。Webhook が無いときはユニットを enable するだけで start しない。Slack は公式 slack 出力ではなく http + `fluent-bit_slack.lua`（JST と `#E01E5A` の赤帯）。URI は `slack.env` の `SLACK_WEBHOOK_URI`。アプリにログ API は足さない。

Grok への繋ぎ方（カスタムコネクタは OAuth 前提）:

1. GitHub secret `MCP_TOKEN` を入れる（未設定ならデプロイが VPS の `.env` に生成する）
2. [grok.com/connectors](https://grok.com/connectors) → New Connector → Custom
3. Server URL: `https://kabukpi.com/mcp`
4. OAuth を聞かれたら:

| 項目 | 値 |
|---|---|
| クライアントID | `stock-analyzer` |
| クライアントシークレット | 空 |
| 認証エンドポイント | `https://kabukpi.com/oauth/authorize` |
| トークンエンドポイント | `https://kabukpi.com/oauth/token` |
| トークン認証方法 | none（PKCEのみ） |

5. ブラウザに許可画面が出るので、`MCP_TOKEN` を入れて「許可する」

Cursor / Grok Bot も同じ OAuth（PKCE、クライアントシークレットなし）。許可する redirect は:

- `https://www.cursor.com/agents/mcp/oauth/callback`
- デスクトップ: `http://localhost:8787/callback`（localhost で許可済）
- フォールバック: `cursor://anysphere.cursor-mcp/oauth/callback`

well-known は Basic 認証の外。`/.well-known/oauth-authorization-server` と `/.well-known/openid-configuration`（同じ AS メタデータ）と `/oauth/` は MCP プロセスへプロキシする。
`/.well-known/` 全体は外さない（acme-challenge は従来どおり）。

MCP 自体が死んだときの退避路は Actions の **Debug VPS**。

## Cloudflare R2 mount と月次 / XBRL バッチ

VPS 上で rclone が bucket `share` を `/home/stock/r2-share` に user mount する。
ユーザ `stock`、`loginctl enable-linger stock` でログアウト・再起動後も残る。systemd ユニットは `deploy/r2-share.service`（秘密情報は含まない）。

見える場所:

- `monthly/{code}/YYYYMMDD_CODE_….pdf|xlsx|html`（`{code}` は TDnet の発行体コードそのまま。9279 / 262A。J-Quants 5桁にはしない）
- `xbrl/{code4}/…zip` と `xbrl/_unmatched/`

初回設定:

1. GitHub secrets に `R2_ACCESS_KEY_ID` と `R2_SECRET_ACCESS_KEY` を入れる
2. Actions → CI/CD → Run workflow、`bump: none` で再デプロイする。
   `.env` の当該行と `/home/stock/.config/rclone/rclone.conf`（chmod 600）が書かれ、user unit `r2-share.service` が mount する。
   secrets が無いときは provision は落とさず、ユニットは enable のみ
3. 月次 PDF の初回バックフィルは Actions → **TDnet monthly backfill** → Run workflow、`days=30`。
   SSH で `scripts/tdnet_monthly_r2.py 30` が走る（通常デプロイでは走らない）

cron（JST）。`run_batch.py` の 19:30 / 06:00 はそのまま。

| スクリプト | cron |
|---|---|
| `scripts/tdnet_monthly_r2.py 1` | 18:00 / 21:00 |
| `scripts/ir_monthly_r2.py` | 10:00 / 16:00 |
| `scripts/ir_monthly_html_r2.py` | 10:20 / 16:20 |
| `scripts/ir_monthly_html_ingest.py` | 10:30 / 16:30（HTML 取得の後。ledger は `/var/log/app/html_ingest_ledger.json`） |
| `scripts/tdnet_xbrl_r2.py 1` | 12–23 時の毎時 00 分 |
| `scripts/xbrl_parse.py` / `scripts/xbrl_normalize.py` | 12–23 時の毎時 10 分 / 20 分 |

振る舞いは `DESIGN.md` §3 と `docs/DESIGN_xbrl.md`。`rclone.conf` と取得ファイルはリポジトリに置かない。

### Cursor Cloud Agent から VPS を触る

経路は2つ。任意シェルは MCP に足さない（`AGENTS.md`）。

| やりたいこと | 経路 |
|---|---|
| ログ・DB 件数・月次 ingest / 訂正 | Cursor の `stock-analyzer` MCP（`https://kabukpi.com/mcp`） |
| `--reparse` や `ir_monthly_html_ingest.py` などシェル | Cloud Agent Secrets の `VPS_SSH_KEY` → `ssh stock-vps` |

`VPS_SSH_KEY` は GitHub Actions と同じデプロイ秘密鍵でよい（`stock` の `authorized_keys` に載っているもの）。チャットに貼らない。

1. Cursor Dashboard → Cloud Agents → Secrets（このリポジトリ scope）に `VPS_SSH_KEY` を登録
2. 新しい Cloud Agent を起動する（既存 VM には入らない。`.cursor/install.sh` が `.cursor/setup-vps-ssh.sh` を呼ぶ）
3. エージェントから:

```bash
ssh stock-vps 'cd /home/stock/stock-analyzer && .venv/bin/python -u scripts/ir_monthly_html_r2.py --reparse'
ssh stock-vps 'cd /home/stock/stock-analyzer && .venv/bin/python -u scripts/ir_monthly_html_ingest.py --ledger /var/log/app/html_ingest_ledger.json'
```

接続確認: `ssh stock-vps 'echo ok $(hostname)'`。失敗したら公開鍵が `stock` の `authorized_keys` にあるか、Actions の **Debug VPS** を見る。

### Cursor Cloud Agent から読む

Cloud Agent の VM には mount が無い。同じ bucket を S3 互換 API で読む。

1. Cursor Dashboard → Cloud Agents → Secrets に `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` を登録する（このリポジトリ scope。R2 側で **Object Read only** のトークンを別に発行する）。
   Secrets は VM 起動時に環境変数として入る。登録後に始めたエージェントから有効
2. `rclone` は `.cursor/install.sh`（環境ビルド）で入る。同じスクリプトが `.venv` / `pdftotext` と、Secrets に `JQUANTS_API_KEY` があれば `stock.db`（2 年分）も作る。`VPS_SSH_KEY` があれば続けて本番の `monthly_issuers` / `monthly_tables` / `monthly_elements` / `monthly_values` だけを載せ替える（DB 丸ごとコピーしない。SSH が無い・繋がらないときは skip）
3. 設定ファイルは書かず、環境変数だけで remote `r2` を定義する

```bash
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ENDPOINT=https://39a6da16651b53d44c459c3a4f4d59f5.r2.cloudflarestorage.com
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"

rclone lsd r2:share/monthly/
rclone ls r2:share/monthly/9279/
rclone copy r2:share/monthly/9279/ /tmp/r2/monthly/9279/   # 必要な分だけ /tmp へ
```

- 取得先は `/tmp` 配下。リポジトリに置かない（`*.pdf` は commit 禁止）
- 書き込みはしない。R2 へ書くのは VPS の cron だけ
- 鍵の値を `echo` やログに出さない
