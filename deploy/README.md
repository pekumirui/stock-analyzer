# VPS 見本 (ConoHa / Ubuntu 24.04)

アプリは作り直さない。パスとユーザ名を自分の環境に合わせてコピーする。

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nginx apache2-utils certbot python3-certbot-nginx

sudo adduser --disabled-password --gecos "" stock
sudo -u stock git clone https://github.com/pekumirui/stock-analyzer.git /home/stock/stock-analyzer
sudo -u stock python3 -m venv /home/stock/stock-analyzer/.venv
sudo -u stock /home/stock/stock-analyzer/.venv/bin/pip install -r /home/stock/stock-analyzer/requirements.txt

# .env は手で置く。JQUANTS_API_KEY のみ
# stock.db は開発PCの backfill 完了後に scp

sudo cp deploy/stock-analyzer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-analyzer

sudo cp deploy/nginx.conf /etc/nginx/sites-available/stock-analyzer
sudo htpasswd -c /etc/nginx/stock-analyzer.htpasswd YOUR_USER
sudo ln -s /etc/nginx/sites-available/stock-analyzer /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# ドメインがあるときだけ
# sudo certbot --nginx -d example.com

sudo -u stock crontab deploy/crontab
```

ファイアウォールは 22 / 80 / 443 だけ開ける。

## CI/CD（main マージで自動リリース）

GitHub Actions (`.github/workflows/cicd.yml`) が `main` へのマージで:

1. テスト (`scripts/ci_smoke.py`)
2. タグと GitHub Release を切る
3. この VPS に SSH して `deploy/update.sh` を実行する

`update.sh` は `git pull --ff-only` → 依存更新 → `scripts/migrate.py` → `systemctl restart`。`.env` と `stock.db` は触らない。

### 一度だけの鍵セットアップ

```bash
# 手元（秘密鍵はリポジトリに置かない）
ssh-keygen -t ed25519 -C "github-actions@stock-analyzer" -f stock-analyzer-deploy -N ""
```

1. `stock-analyzer-deploy`（秘密鍵）を GitHub → **Settings → Secrets and variables → Actions** に `VPS_SSH_KEY` として登録
2. 任意: ホストを変えるときだけ `VPS_HOST` / `VPS_USER` / `VPS_PORT`（未設定なら `160.251.179.50` / `stock` / `22`）
3. VPS で公開鍵と sudoers を入れる:

```bash
ssh -t root@YOUR_HOST "cd /home/stock/stock-analyzer && sudo bash deploy/enable-cicd.sh '$(cat stock-analyzer-deploy.pub)'"
```

ホスト鍵は `deploy/known_hosts` にピン留めしてある。VPS を再作成・再起動でホスト鍵が変わったら `ssh-keyscan YOUR_HOST` で更新する。

ConoHa コンソールのキーペアは **root** 用。Actions は `stock` ユーザで入るので、コンソール鍵を `VPS_SSH_KEY` に入れただけでは足りない。上の `enable-cicd.sh` で stock の `authorized_keys` に GitHub 用の公開鍵を入れる。

`VPS_SSH_KEY` が無い間は Release まで行い、デプロイ job はスキップする。失敗したデプロイのやり直しは Actions の **Run workflow**（`bump: none`）。