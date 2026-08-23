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

sudoers を更新したあとは、既存 VPS でも **もう一度 `enable-cicd.sh` を実行する**。

## GitHub Secrets

| Name | 必須 | 内容 |
|---|---|---|
| `VPS_SSH_KEY` | はい | デプロイ用秘密鍵 |
| `JQUANTS_API_KEY` | 推奨 | あるときだけ `.env` の当該行を更新。無ければ既存 `.env` を触らない |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | 推奨 | nginx Basic 認証。無ければ既存 `htpasswd` を残す |
| `VPS_DOMAIN` | 任意 | 未設定なら `160.251.179.50.sslip.io` |
| `VPS_HOST` / `VPS_USER` / `VPS_PORT` | 任意 | 未設定なら `160.251.179.50` / `stock` / `22` |
| `CERTBOT_EMAIL` | 任意 | 無いと certbot は `--register-unsafely-without-email` |

`stock.db` はパイプラインが触らない。初回データは手元で backfill して scp するか、VPS 上で `scripts/run_batch.py`。

## パイプラインが毎回やること

`update.sh`:

1. `git pull --ff-only origin main`
2. `JQUANTS_API_KEY` があれば `.env` のその行だけ upsert
3. `sudo provision.sh`（パッケージ、systemd、nginx、ufw 22/80/443、Let's Encrypt、crontab）
4. venv / `pip install` / `migrate.py`
5. `systemctl restart stock-analyzer` と localhost:8000 のヘルスチェック

ブラウザは [https://160.251.179.50.sslip.io/](https://160.251.179.50.sslip.io/)（iPhone の HTTPS-Only 対策）。

ホスト鍵は `deploy/known_hosts` にピン留め。VPS を作り直したら `ssh-keyscan YOUR_HOST` で更新する。

失敗したデプロイのやり直しは Actions の **Run workflow**（`bump: none`）。
