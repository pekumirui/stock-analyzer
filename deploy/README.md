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
