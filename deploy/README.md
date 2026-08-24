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
- リポジトリ clone（無ければ）
- `authorized_keys` にデプロイ公開鍵
- `/etc/sudoers.d/stock-analyzer`（`provision.sh` と `systemctl` だけ）

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

1. `git pull --ff-only origin main`
2. `JQUANTS_API_KEY` / `MCP_TOKEN` があれば `.env` のその行だけ upsert
3. `sudo provision.sh`（パッケージ、systemd、nginx、ufw 22/80/443、Let's Encrypt、crontab、sudoers）
4. venv / `pip install` / `migrate.py`
5. `systemctl restart stock-analyzer` と `stock-analyzer-mcp`、localhost:8000 / :8010 のヘルスチェック

証明書の更新は `certbot.timer`（1日2回）。更新後は `renewal-hooks/deploy` で nginx を reload する。

ブラウザは [https://160.251.179.50.sslip.io/](https://160.251.179.50.sslip.io/)（iPhone の HTTPS-Only 対策）。

ホスト鍵は `deploy/known_hosts` にピン留め。VPS を作り直したら `ssh-keyscan YOUR_HOST` で更新する。

失敗したデプロイのやり直しは Actions の **Run workflow**（`bump: none`）。

## ログ MCP（Grok）

VPS 上で `deploy/log_mcp.py` が `127.0.0.1:8010` を待ち、nginx が Basic 認証なしで `/mcp` だけプロキシする。ポート 8010 は外に開けない。

ツール:

- `service_status` — プロセスと localhost の HTTP コード
- `app_logs` — `journalctl -u stock-analyzer`
- `batch_logs` — `batch.log`
- `nginx_logs` — nginx journal と error.log

任意コマンドも restart もない。行数は最大 500。`.env` の値は伏せる。

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
