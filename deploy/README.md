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

リポジトリはプライベート。VPS の `stock` ユーザが `git pull` するには GitHub Deploy key（read-only）が必要。`setup-github-ssh.sh` が鍵を作り公開鍵を出すので、GitHub → Settings → Deploy keys に一度だけ登録する。clone が鍵未登録で落ちたら、出した公開鍵を入れてから `enable-cicd.sh` を再実行。

sudoers を更新したあとは、既存 VPS でも **もう一度 `enable-cicd.sh` を実行する**。ただし `provision.sh` も sudoers を冪等に入れるので、通常の再デプロイだけでも足りる。

## GitHub Secrets

| Name | 必須 | 内容 |
|---|---|---|
| `VPS_SSH_KEY` | はい | デプロイ用秘密鍵 |
| `JQUANTS_API_KEY` | 推奨 | あるときだけ `.env` の当該行を更新。無ければ既存 `.env` を触らない |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | 推奨 | nginx Basic 認証。無ければ既存 `htpasswd` を残す |
| `MCP_TOKEN` | 推奨 | ログ MCP の Bearer トークン。`openssl rand -hex 32`。無ければ VPS 上で生成（値は CI に出ない） |
| `VPS_DOMAIN` | 任意 | 未設定なら `160.251.179.50.sslip.io` |
| `VPS_HOST` / `VPS_USER` / `VPS_PORT` | 任意 | 未設定なら `160.251.179.50` / `stock` / `22` |
| `CERTBOT_EMAIL` | 任意 | 無いと certbot は `--register-unsafely-without-email` |

`stock.db` はパイプラインが触らない。初回データは手元で backfill して scp するか、VPS 上で `scripts/run_batch.py`。

## パイプラインが毎回やること

`update.sh`:

1. GitHub SSH（deploy key）を冪等に揃えてから `git pull --ff-only origin main`
2. `JQUANTS_API_KEY` / `MCP_TOKEN` があれば `.env` のその行だけ upsert
3. `sudo provision.sh`（パッケージ、systemd、nginx、ufw 22/80/443、Let's Encrypt、crontab、sudoers）
4. venv / `pip install` / `migrate.py`
5. `systemctl restart stock-analyzer` と `stock-analyzer-mcp`、localhost:8000 / :8010 のヘルスチェック

証明書の更新は `certbot.timer`（1日2回）。更新後は `renewal-hooks/deploy` で nginx を reload する。

ブラウザは [https://160.251.179.50.sslip.io/](https://160.251.179.50.sslip.io/)（iPhone の HTTPS-Only 対策）。

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

任意コマンドも restart もない。行数は最大 500。`.env` の値は伏せる。

アプリ・バッチ・MCP のファイルログは `/var/log/app/`。14 日で logrotate（copytruncate）。nginx は従来どおり `/var/log/nginx/`。

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

well-known は Basic 認証の外。`/.well-known/oauth-authorization-server` と `/oauth/` は MCP プロセスへプロキシする。

MCP 自体が死んだときの退避路は Actions の **Debug VPS**。
