# VPS (ConoHa / Ubuntu 24.04)

手で nginx / ufw / systemd を積む必要はない。**root でデプロイ鍵を一度入れる**と、あとは GitHub Actions が冪等に初期設定する。

`deploy/provision.sh` が apt / nginx / ufw / certbot / systemd / crontab を冪等に揃える。
nginx の設定の正本は `deploy/nginx-site.sh`（`provision.sh` が source して `/etc/nginx` に書く）。CI の `tests/ops/test_nginx_public.py` が同じものを組み立てて `nginx -t` に通し、起動して公開 / Access / MCP の振り分けを確かめる。

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

sudoers を更新したあとは、既存 VPS でも **もう一度 `enable-cicd.sh` を実行する**。通常の再デプロイでも足りる。`update.sh` はフルの `sudo provision.sh` の前に `provision.sh --sudoers-only` で `/etc/sudoers.d` を入れ、新しい `env_keep` を同じデプロイの `--preserve-env` で渡せる。

## GitHub Secrets

| Name | 必須 | 内容 |
|---|---|---|
| `VPS_SSH_KEY` | はい | デプロイ用秘密鍵 |
| `JQUANTS_API_KEY` | 推奨 | あるときだけ `.env` の当該行を更新。無ければ既存 `.env` を触らない |
| `EDINET_API_KEY` | 任意 | EDINET API v2 のキー（`docs/DESIGN_edinet.md`）。あるときだけ `.env` の当該行を更新。無ければ `jobs/edinet/edinet_documents.py` は何もしない（コードリストはキー不要） |
| `ANTHROPIC_API_KEY` | 任意 | Claude API のキー（従量課金。Console で利用額の上限を付ける）。あるときだけ `.env` の当該行を更新。無ければ `jobs/market/briefing_extract.py` の submit / collect は動かない（`--dry-run` と `status` は動く） |
| `ESTAT_APP_ID` | 任意 | e-Stat API のアプリケーション ID（サプライタブの月次量）。あるときだけ `.env` の当該行を更新。無ければ `jobs/supply/material_stats.py --source estat` は何もしない（経産省 Excel はキー不要） |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | 残置 | 残置の rclone FUSE（`/home/stock/r2-share`）用。ジョブの dest は `data/`。あるときだけ `.env` と `~/.config/rclone/rclone.conf` の `[r2]` を upsert（他 remote は残す）。無ければ既存を触らない |
| `SLACK_WEBHOOK_URL` | 推奨 | Slack Incoming Webhook（#alerts）。コミットしない。漏れたら rotate。provision が `/etc/fluent-bit/slack.env` に URL と `SLACK_WEBHOOK_URI` を書く。`.env` には入れない |
| `SLACK_WEBHOOK_URL_APP` | 任意 | Incoming Webhook（#alerts-app、fluent-bit タグ `app`）。未設定・空なら `SLACK_WEBHOOK_URL`（#alerts）にフォールバック。`.env` には入れない |
| `SLACK_WEBHOOK_URL_BATCH` | 任意 | Incoming Webhook（#alerts-batch、タグ `batch`）。未設定・空なら #alerts にフォールバック |
| `SLACK_WEBHOOK_URL_INFRA` | 任意 | Incoming Webhook（#alerts-infra、タグ `nginx` / `mcp` / `systemd`）。未設定・空なら #alerts にフォールバック |
| `SLACK_WEBHOOK_URL_QUIET` | 任意 | Incoming Webhook（#alerts-quiet、タグ `quiet`。nginx の `limiting connections by zone` と再起動中の `connect() failed (111: Connection refused)`）。未設定・空なら `SLACK_WEBHOOK_URI_INFRA`（#alerts-infra）にフォールバック。`.env` には入れない |
| `MCP_TOKEN` | 推奨 | ログ MCP の Bearer トークン。`openssl rand -hex 32`。無ければ VPS 上で生成（値は CI に出ない） |
| `VPS_DOMAIN` | 任意 | 未設定なら `160.251.179.50.sslip.io` |
| `VPS_HOST` / `VPS_USER` / `VPS_PORT` | 任意 | 未設定なら `160.251.179.50` / `stock` / `22` |
| `CERTBOT_EMAIL` | 任意 | 無いと certbot は `--register-unsafely-without-email` |

GitHub Variables（秘密ではない）:

| Name | 内容 |
|---|---|
| `CF_ACCESS_TEAM_DOMAIN` / `CF_ACCESS_AUD` | Cloudflare Access のチームのドメイン（`<team>.cloudflareaccess.com`）と Application の AUD。あるときだけ `.env` の当該行を更新。無ければ Access の証明は通らず `/app` などは 401 |

`stock.db` はパイプラインが触らない。初回データは手元で backfill して scp するか、VPS 上で `jobs/market/run_batch.py`。

## パイプラインが毎回やること

`update.sh`:

1. GitHub SSH（deploy key）を冪等に揃えてから `git pull --ff-only origin main`
2. `JQUANTS_API_KEY` / `ESTAT_APP_ID` / `EDINET_API_KEY` / `MCP_TOKEN` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` があれば `.env` のその行だけ upsert。R2 の両方が揃っていれば `~/.config/rclone/rclone.conf` の `[r2]` だけ upsert（`onedrive` など他 remote は消さない）。`SLACK_WEBHOOK_URL` と `SLACK_WEBHOOK_URL_{APP,BATCH,INFRA,QUIET}` は `.env` に書かない
3. `sudo provision.sh --sudoers-only`（`APP_DIR` だけ preserve。live sudoers を repo に合わせる）→ `sudo provision.sh --defer-reboot`（apt のセキュリティ更新、`linux-image-generic` と `linux-headers-generic` だけの `--only-upgrade`、CVE-2026-98432 の nftables 緩和、パッケージ、systemd、nginx、ufw 22/80/443、Let's Encrypt、crontab、sudoers、R2 user mount、fluent-bit。Slack webhook は `--preserve-env`）。Webhook が空なら fluent-bit は enable のみで start しない。この段では再起動しない
4. venv / `pip install` / `migrate.py`（`database is locked` は最大約 6 分まで再試行。19:30 の日次バッチや毎時の TDnet XBRL（parse / normalize）と重なるため）。`migrate.py` が `init_db` を 1 回やってから uvicorn を上げる（`--workers N` の同時 CREATE INDEX を避ける。`--preload` は使わない）
5. `systemctl restart stock-analyzer` と `stock-analyzer-mcp`、localhost:8000 / :8010 のヘルスチェック。成功したあと `sudo provision.sh --kernel-reboot-only`。`/var/run/reboot-required` があり、`/home/stock/jobs/*/pid` の生きているプロセスも `deploy/cron-run.sh` も無ければ `shutdown -r +1`。ジョブが動いていれば再起動せず `reboot pending`。ヘルスチェックが失敗したデプロイは再起動しない

uvicorn のワーカー数は `deploy/uvicorn_workers.py` が起動時に決める（`nproc` と MemAvailable。1 本あたり 384MiB。上限 2 本）。`.env` の `UVICORN_WORKERS` で上書きする。ワーカーのヘルスチェックは 10 秒（`deploy/uvicorn-start.sh`）。日次バッチ中にスワップへ出たワーカーが 5 秒で kill され 502 になったため（KAB-366）。MCP（`stock-analyzer-mcp.service`）は対象外。

証明書の更新は `certbot.timer`（1日2回）。更新後は `renewal-hooks/deploy` で nginx を reload する。

ブラウザは [https://kabukpi.com/monthly](https://kabukpi.com/monthly) が公開の月次。個人ツールは [https://kabukpi.com/app](https://kabukpi.com/app)（Cloudflare Access。下の「Cloudflare」）。証明書は RSA + ISRG Root X1。ECDSA 既定の Root YE は端末にまだ無く「セキュリティ保護なし」になる。

公開 location は `/monthly`・GET `/api/v1/tables`（code/year まで）・`/static/`・`robots.txt` / `sitemap.xml`。書き込み API と `/app` は Access。公開側に `limit_req` は付けない。`/static/` は `$APP_DIR/static/` を nginx が `alias` で直配信する（`Cache-Control: no-cache`）。`/static/monthly_row_order.json` と `/static/ir_monthly_codes.json` だけ uvicorn。provision が `www-data` 用に `$HOME` へ `o+x`、`static/` へ `a+rX` を付け、`gzip_types` に css/js を入れる（`conf.d/stock-analyzer-gzip.conf`）。

ホスト鍵は `deploy/known_hosts` にピン留め。VPS を作り直したら `ssh-keyscan YOUR_HOST` で更新する。

## Cloudflare（プロキシと Access）

kabukpi.com の DNS は Cloudflare。A レコードをプロキシ（オレンジの雲）にし、ログインが要る場所を Cloudflare Access で守る。

nginx の認証の場所（`auth_block`）は `auth_request` だけで、Access の証明は Cloudflare が付ける `Cf-Access-Jwt-Assertion` を nginx の `auth_request` が uvicorn の `/_auth/cf-access`（`app/cf_access.py`）に渡し、署名・AUD・発行元・期限を確かめる。ヘッダーは偽造できるので、Cloudflare の IP からかどうかでは判断しない。VPS の IP に直接来たアクセスや Access の証明が無いアクセスは 401。`CF_ACCESS_TEAM_DOMAIN` / `CF_ACCESS_AUD` が無いと全部 401 になる。`conf.d/stock-analyzer-realip.conf` が Cloudflare の IP 範囲（[一覧](https://www.cloudflare.com/ips/)）からの `CF-Connecting-IP` を送り元として扱う（`limit_conn` とアクセスログ）。範囲が変わったら `deploy/nginx-site.sh` の `CF_IPS` を直す。

ダッシュボードの手順（順番どおり）:

1. nginx と `app/cf_access.py` をデプロイする（プロキシ前なので何も変わらない）
2. **SSL/TLS** → **Overview** を **Full (strict)**。VPS の Let's Encrypt の証明書をそのまま使う
3. **DNS** → `kabukpi.com`（と `www`）の A レコードを **Proxied** にする。SSH と Actions のデプロイは IP 直なので影響しない
4. Zero Trust → **Access** → **Applications** → **Add** → **Self-hosted**。ログイン方法は wiki と同じ One-time PIN、ポリシーは **Allow**・Include は **Emails** に本人のアドレスだけ。**Session Duration** は最長（1 か月）
5. 同じ Application の公開しない Hostname / Path: `kabukpi.com/app`、`kabukpi.com/api`、`kabukpi.com/docs`、`kabukpi.com/redoc`、`kabukpi.com/openapi.json`
6. もう 1 つ Self-hosted の Application を `kabukpi.com/api/v1/tables` で作り、ポリシーは **Bypass**・Include は **Everyone**。公開の `/monthly` の画面がこの GET を読む。Access はより長いパスの Application を使う。SPA はここに書き込まない（書き込みは MCP と loopback）
7. 1 つめの Application の **Overview** の **Application Audience (AUD) Tag** を GitHub Variables `CF_ACCESS_AUD`、チームのドメイン（**Settings** → **Custom Pages** の Team domain）を `CF_ACCESS_TEAM_DOMAIN` に入れてデプロイし直す
8. 確認: 未ログインのブラウザで `/app` が Access の画面になり、直接 IP で `/app` を開くと 401。`/monthly`・`/api/v1/tables`・`/mcp`（Claude.ai のコネクタ）が今までどおり

Cloudflare 経由では、オリジンの応答が約 100 秒止まると 524 で切れる（Free）。`/mcp` の `db_query` は約 2 分まで待つので、100 秒を超える問い合わせは切れる。

戻すとき: DNS の A レコードを **DNS only** に戻す。Access の証明が来なくなるので `/app` などは 401 のまま（Basic 認証は無い）。

## CVE-2026-98432（IPv4 TCP の緩和とカーネル）

`provision.sh` が [Ubuntu の緩和](https://ubuntu.com/security/CVE-2026-98432)を入れる。IPv4 オプション付きの TCP だけを落とし、オプションの無い TCP（SSH、80/443、`/mcp`）は通す。TCP のタイムスタンプやウィンドウスケーリング、IPv6 は対象外。Ubuntu の注記どおり、リスクは下がるが完全には防げない。noble-updates に新しいメタパッケージがあれば、同じ provision が `linux-image-generic` と `linux-headers-generic` だけを上げて、条件を満たすとき再起動する。

`deploy/cve-2026-98432.nft`:

```
table inet cve_2026_98432 {
	chain input {
		type filter hook input priority -310; policy accept;
		ip protocol tcp ip hdrlength > 5 counter drop
	}
}
```

`/etc/cve-2026-98432.nft` に置き、`nft -c -f` のあと `nft -f` で読む。再起動後は `cve-2026-98432.service` が同じファイルだけを読み込む。distro の `nftables.service` は `ExecStop` が `nft flush ruleset` で、既定の `/etc/nftables.conf` も `flush ruleset` なので有効にしない（有効だと ufw のルールが消える）。既に有効なら disable する（stop / restart はしない）。ufw の 22/80/443 はそのまま。

同じ provision で `apt` / `apt-utils` / `libapt-pkg6.0t64` だけ `--only-upgrade` する（CVE-2026-109518、KAB-359 の残り）。apt 自身の取得は `Acquire::Retries=0`。この更新はカーネルを指名しない。稼働中の nginx が差し替え済みの libpcre2 を掴んだままなら reload する。

その直後に `linux-image-generic` と `linux-headers-generic` だけ `apt-get install -y --only-upgrade` する（`DEBIAN_FRONTEND=noninteractive`、`Acquire::ForceIPv4=true`）。他のパッケージは指名しない。`/var/run/reboot-required` が無いときは再起動しない。あるとき、`/home/stock/jobs`（stock の `~/jobs`）の `*/pid` に生きているプロセス、そのディレクトリ（またはその下）を cwd にしているプロセス、または引数が `deploy/cron-run.sh` のように `/cron-run.sh` で終わるプロセスがあれば再起動せず `reboot pending` と出して終わる。無ければ `stock-analyzer` と `stock-analyzer-mcp` が enable であることを確認し（`cve-2026-98432.service` の unit があればそれも）、`shutdown -r +1` する。`/run/systemd/shutdown/scheduled` が既にあれば予約しない。`/etc/cve-2026-98432.nft` は消さない。

`update.sh` は provision に `--defer-reboot` を渡す。pip / migrate は 1 分を超えることがあり、provision の途中で `shutdown -r +1` するとデプロイの SSH が切れる。ヘルスチェックの成功後に `--kernel-reboot-only` で同じ判定をする。手動の `sudo provision.sh`（`--defer-reboot` なし）は provision の最後に同じ判定をする。2 回目は、メタパッケージが最新なら apt は何も入れず、再起動が予約済みか `reboot-required` が無ければ `shutdown` を呼ばない。

デプロイ後:

```bash
sudo nft list table inet cve_2026_98432
sudo systemctl is-enabled cve-2026-98432.service
sudo ufw status
apt-cache policy apt | head -n 3
if sudo grep -q 'libpcre2.*deleted' /proc/"$(pgrep -o nginx)"/maps; then
  echo "nginx still maps a deleted libpcre2"
fi
```

SSH、[https://kabukpi.com/](https://kabukpi.com/) 、[https://kabukpi.com/mcp](https://kabukpi.com/mcp) がつながること。`ufw status` は 22/80/443 のまま。`apt` は 2.8.4ubuntu0.1 以上。nginx の maps に `libpcre2` の `(deleted)` が無いこと。

戻し方（このテーブルだけ外す。ufw は触らない）:

```bash
sudo systemctl disable --now cve-2026-98432.service
sudo rm -f /etc/systemd/system/cve-2026-98432.service /etc/cve-2026-98432.nft
sudo systemctl daemon-reload
if sudo nft list table inet cve_2026_98432 >/dev/null 2>&1; then
  sudo nft delete table inet cve_2026_98432
fi
```

`disable --now` の停止処理がこのテーブルだけを消す（`flush ruleset` はしない）。`nftables.service` は ufw と併用しているあいだ有効にしない。

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

## box からの読み取り専用 SFTP（boxro）

box エージェントはユーザ `boxro` で sftp するだけ（シェルもポート転送も不可）。chroot は `/srv/boxro`。`provision.sh` が冪等に揃える。

見えるもの:

| chroot 内 | 実体 |
|---|---|
| `/data` | `$APP_DIR/data`（`monthly` / `xbrl` / `inventory` / `stats` / `xbrl_golden` / `tdnet_pdf_golden` / `xbrl_taxonomy`）。box からは `/mnt/vps/data` |

見せない: `/var/log/app`（ログは MCP の `app_logs` / `batch_logs` と `fs_*`）、`stock.db`、`.env`、`/home/stock/backups`、R2 FUSE（`/home/stock/r2-share`）。`data/` は既に bind しているので、`data/monthly` と `data/xbrl` は boxro から見える。FUSE は bind しない。既存ホストに `/srv/boxro/logs` の bind が残っていれば provision が umount し、fstab のその 1 行だけ消して空なら rmdir する（`/var/log/app` の中身は触らない）。

公開鍵は `deploy/ssh/boxro_authorized_keys`（1行1鍵、先頭 `restrict`）。provision が `/etc/ssh/authorized_keys/boxro`（root:root、644）へ同期する。鍵を足すときはそのファイルに行を足してデプロイ。`/etc/ssh/sshd_config.d/90-boxro.conf` が変わったときだけ `sshd -t && systemctl reload ssh`。`sshd -t` が失敗したら drop-in を戻す。restart はしない。boxro の失敗は stderr に出すが、アプリの provision（systemd / nginx / crontab）は続ける。既存の stock / root / デプロイ鍵には触らない。

Cursor のクラウドエージェントは Secret `cursor_vps_ro`（秘密鍵）で `boxro@160.251.179.50` に sftp / sshfs する。見えるのは `data/` だけ。

元に戻す（root）:

```bash
rm -f /etc/ssh/sshd_config.d/90-boxro.conf /etc/ssh/authorized_keys/boxro
sshd -t && systemctl reload ssh
umount /srv/boxro/data
# /etc/fstab の /srv/boxro/data の 1 行を消す（logs 行は provision が消す）
userdel boxro
rmdir /srv/boxro/data /srv/boxro
```

失敗したデプロイのやり直しは Actions の **Run workflow**（`bump: none`）。

## box から data/monthly へ PDF を追加する（monthly_push）

R2 廃止後、TDnet に無い古い月次 PDF を box から VPS の `data/monthly/<code>/` に置く経路（KAB-135）。ユーザは `stock` のまま（既存のデプロイ鍵・admin 鍵は変えない）。鍵の `restrict,command=` でシェルと転送を禁止し、`rrsync -wo` で **書き込みのみ**・**`data/monthly` 配下のみ**。IP 制限はしない（box の IP は固定でない）。

`provision.sh` が冪等に揃える（`deploy/monthly_push.sh`。boxro と同じく失敗してもアプリの provision は続ける。`enable-cicd.sh` は触らない。初回クローンのあとのデプロイで入る）。`sshd` の reload / restart はしない（`authorized_keys` を足すだけ）。パッケージ `rsync`（`/usr/bin/rrsync`）を apt で入れる。`data/monthly` が無ければ作る（所有者 `stock`）。

公開鍵は `deploy/ssh/monthly_push_authorized_keys`（1行1鍵、先頭 `restrict,command="/usr/bin/rrsync -wo …/data/monthly"`）。provision が `stock` の `~/.ssh/authorized_keys` へ **追加だけ**する（既にあれば何もしない。無い行だけ足す。既存行は消さない・書き換えない）。同じ公開鍵が制限なしで既にあるときは足さず失敗する（sshd は先に書いた行を使うため）。root の `authorized_keys` と `deploy/admin_keys` と boxro には入れない。秘密鍵は box の `~/.ssh/vps_monthly_push`。リポジトリと GitHub Secrets には置かない。

box からの送り方（クライアント。`--ignore-existing` で既存は SKIP。サーバ側は上書きを禁止しない）:

```bash
rsync -a --ignore-existing -e 'ssh -i ~/.ssh/vps_monthly_push -o IdentitiesOnly=yes' \
  ./YYYYMMDD_CODE_….pdf stock@160.251.179.50:CODE/
```

`CODE/` は rrsync のルート（`$APP_DIR/data/monthly`）からの相対。実体は `data/monthly/<code>/`。`ssh -i ~/.ssh/vps_monthly_push stock@HOST` ではシェルは取れない。

元に戻す（root。既存の stock 鍵は残す）:

```bash
# stock の authorized_keys から box-monthly-push の 1 行だけ消す
```

失敗したデプロイのやり直しは Actions の **Run workflow**（`bump: none`）。

## ログ MCP（Grok）

VPS 上で `mcp/log_mcp.py` が `127.0.0.1:8010` を待ち、nginx が Access なしで `/mcp` だけプロキシする。ポート 8010 は外に開けない。

ツール:

- `service_status` — プロセス（監視の `monitoring-*` も）と localhost:80 / :8000 の HTTP コード。MCP 自身（:8010）は自己 curl せず、この応答自体を ok とする
- `app_logs` — `/var/log/app/stock-analyzer.log` と journalctl。uvicorn は `deploy/uvicorn-log.json` で行頭に JST `YYYY-MM-DD HH:MM:SS` を付ける。`since` / `until` は `batch_logs` と同じ形。省略時は journal が直近 1h、ファイルは live の末尾だけ。指定すると `.1` / `.gz` も読み、行頭の時刻で絞る。時刻の無い行（Traceback の続き）は直前の行に付く
- `batch_logs` — `/var/log/app/<ジョブ>.log`。`job` は crontab / cron-run.sh の stem（`run_batch` / `pts_snapshot` / `fx_rates` / `tdnet_monthly_r2` / `tdnet_briefing_r2` / `tanshin_covers` / `tdnet_monthly_pdf_sidecar` / `tdnet_monthly_pdf_ingest` / `ir_monthly_r2` / `ir_monthly_html_r2` / `ir_monthly_html_ingest` / `ir_monthly_json_ingest` / `ir_monthly_xls_ingest` / `tdnet_xbrl_r2` / `xbrl_parse` / `xbrl_normalize` / `xbrl_statements` / `edinet_codes` / `edinet_documents` / `edinet_xbrl` / `edinet_large_holdings` / `edinet_tender_offers` / `edinet_buybacks` / `edinet_archive` / `vps_packages_r2` / `backup_db` / `backup_data` / `migrate_r2_share` / `irbank_ingest` / `briefing_extract` / `briefing_offsite` / `homepage_briefing`）。一覧に無い名前とパスは拒否。省略時は `stock-analyzer.log` と `mcp.log` 以外の live ファイル（古い `batch.log` を含む）。`since` / `until` は `1h` / `30m` / `today` / `yesterday` / `YYYY-MM-DD`。`until` はその時刻を含まない。昨日は `since=yesterday` と `until=today`。日時を指定したときは `.1` と `.gz` も読み、行頭の JST `YYYY-MM-DD HH:MM:SS` で絞る。`lines` は 1-500。超えた分は末尾だけ返すので範囲を狭める
- `nginx_logs` — nginx journal と error.log と `/var/log/nginx/stock-analyzer.access.log`。アクセスログの `rt=` は nginx 全体、`urt=` は uvicorn の処理秒。遅いリクエストは `grep` に `rt=[1-9]` などを渡す。`since` / `until` は `batch_logs` と同じ形。省略時は journal が直近 1h、ファイルは live の末尾だけ。指定すると logrotate 済みの `.1` / `.gz`（既定で 14 日分）も読み、行の時刻（JST）で絞る。URL の OAuth 一時値（`code` / `state` など）は伏せる
- `fs_list` / `fs_read` / `fs_grep` — 読み取り専用。`root` は `data`（リポジトリの `data/`、`MCP_DATA_DIR`）か `logs`（`/var/log/app`、`MCP_LOG_DIR`）。それ以外・絶対パス・`..`・symlink で root の外へ出るものは拒否。`.env*` / `*.db` / `*.sqlite*` / `stock.db` / `*.pem` / `id_*` / `*.key` / `rclone.conf` と `/home/stock/backups` / `/home/stock/r2-share` は二重に拒否。`fs_read` は 1 回あたり `MCP_FS_MAX_BYTES`（既定 1MiB）、切り詰め時は `truncated` とファイル全体の `size` を返す。認証は他ツールと同じ `MCP_TOKEN` / OAuth
- `db_stats` — stock.db の件数。固定9テーブルは日付範囲・デモ混在つき。残りは `sqlite_master` から件数（`sqlite_*` 除く）。既定は `MAX(rowid)` の概算（削除があると多め）。`exact=true` で `COUNT(*)`。`tables` で対象を絞る。`daily_prices` の codes/days は exact のときだけ。1クエリ約2秒・全体約8秒で打ち切り、超えた表は `(timeout)`
- `db_lookup` — 1銘柄のマスタ・直近株価・決算・指標
- `db_query` — stock.db に SELECT / WITH を 1 文だけ実行。先頭の `EXPLAIN` / `EXPLAIN QUERY PLAN` も可。読み取り専用（`open_db()` の `mode=ro` と `PRAGMA query_only=ON`、`set_authorizer` で READ / SELECT / FUNCTION / RECURSIVE と schema 用 PRAGMA 以外を拒否）。INSERT / UPDATE / DELETE / DROP / ATTACH / PRAGMA 書き込み / 複数文 / CTE 内の書き込みは拒否。`EXPLAIN` の後ろも SELECT / WITH 以外は拒否。`limit` は 1-2000（既定 200）。超えた行と出力バイト（約 1MiB）は `truncated`。約 2 分で打ち切り。`params` は `?` の配列か `:name` のオブジェクト。OAuth トークンは `.mcp-oauth.json`（DB 外）。秘密表（名前に token/secret/password/credential）は拒否。実行 SQL（先頭 400 文字）・所要時間・行数を `mcp.log` に書く
- `db_schema` — テーブル/ビューと列（`sqlite_master` と `pragma_table_info`）。`sqlite_*` と秘密表は出さない。`tables` で対象を絞る。読み取り専用
- `xbrl_compare` — 1銘柄の実績を期末（period_end）ごとに statements / TDnet XBRL / EDINET XBRL で並べる。既定は直近 8 期。`period_end`（YYYY-MM-DD）で 1 期だけ。`items` で科目を絞る（std コード `operating_income` か statements の列名 `operating_profit`）。各期の行に各 source の doc_id・提出日・連結/単体・訂正（AMENDED）。判定は st:td / st:ed / td:ed。OK / DIFF / MISSING。読み取り専用
- `xbrl_diff_summary` — 市場全体で XBRL（既定 edinet。`source=tdnet` も可）と statements を期末で突き合わせる。期の件数（XBRL の期・statements と組めた期・XBRL だけの期）、科目別・会計基準別・33業種別の一致率、ずれの大きい DIFF 上位 `limit` 件（既定 20、最大 500）。`since`（YYYY-MM-DD）は xbrl_filings.submitted_at。全期間は重いので since で絞る。約 9 秒で打ち切る
- 判定: 両方ある・符号が逆 → DIFF。statements との金額差が 100 万円未満なら OK（J-Quants の百万円丸め）。td:ed にその許容は無い。EPS も丸め許容なし。相対差 > 0.1% → DIFF。片方が空 → MISSING。四半期は累計のまま。1 期 1 書類（提出が新しい方。訂正も含む）、その書類に連結があれば連結
- `metrics_query` — VictoriaMetrics（`127.0.0.1:8428`）に PromQL / MetricsQL を GET で投げるだけ（`/api/v1/query` / `query_range` / メトリクス名一覧）。`start` が無ければ instant、あれば range。時刻は `batch_logs` と同じ形（JST）。1 系列 1100 点まで（`step` 省略時は自動）、200 系列まで、応答は約 1MiB で打ち切り。`list_metrics=true` で名前の一覧。主な名前は下の「監視」
- `fx_rate` — 指定日の為替。EUR ベースのクロス（例 USD/JPY）。休日・ECB非公表日は直近営業日（`as_of`）。`holiday_fallback` で明示
- `fx_rates` — 期間の為替（公表日のみ）を TSV で。値は有効数字 5 桁。先頭に始め・終わり・変化率・高値・安値（期間の日次の全公表日から）。`interval` は `day`（既定）/ `week` / `month`（週・月の最後の公表日）。休日を直近営業日で埋めたいときは `fx_rate`
- `monthly_list` — 月次表一覧。localhost:8000 の GET /api/v1/tables
- `monthly_get` — 銘柄×年度の月次表。values は 1〜12。無い月は null
- `monthly_add_element` — 要素登録
- `monthly_put_values` — セル一括 upsert（null で削除）。year は暦年。訂正用
- `monthly_delete_element` — 値の無い行を消す。値が残っていれば 409。表は残す
- `monthly_get_doc` / `monthly_put_doc` — 年の表を 1 枚の文書で読んで、直して丸ごと返す。行名はそのまま（正規化・エイリアスなし）。改名は `was`、文書に無い行は削除。構造の変更は `allow_structure_change`、`dry_run` で差分だけ。仕様は `DESIGN.md` の API 表
- `monthly_set_industry` — 月次業種。PUT /api/v1/tables/{code}/industry。銘柄マスタの業種とは別
- `monthly_ingest` — PDF 月次の新規登録。POST /api/v1/tables/{code}/ingest。year は渡さない。
  列順 + as_of 時点の最新 `fiscal_year_end` から暦年を決める。月が重複する year-wrap は列順配列の `values`。`dry_run: true` は書かずに年割当と書くはずのセルを返す。`only_months` は `YYYY-MM` で列を絞る
- 決算ツール（`mcp/earnings_tools.py`。KAB-404）— 決算速報画面と同じ API の数字を TSV に詰めて返す。サーバー側で並べ替えの既定・採点・足切りは持たない。どれも読み取りだけ
  - `earnings_day` — `GET /api/earnings` の 1 日分。開示 1 件 1 行と未発表の銘柄。列は業種・時刻・Q・通期営利予想の上方 / 下方・訂正・PTS 終値比・終値・時価総額・予想PER・売上 / 営利 / 経利 / 純利の累計と YoY・3か月と QoQ（百万円）・営利進捗率と前年同期・決算翌日の騰落と対TOPIX・`disclosure_no`。終値と時価総額はその日以前の直近の `daily_prices`、翌日の騰落は `earnings_events` を stock.db から読む。PTS は画面と同じく株探ランキング（各 75 位）に載った銘柄だけ。`sort`（`time` / `pts_up` / `pts_down` / `pts_abs`）・`sectors`・`quarters`・`codes`・`offset` / `limit`（既定 150、最大 1000）。集中日の API は数秒かかるので待ちは 60 秒
  - `earnings_detail` — `GET /api/earnings/detail/{code}`。累計・3か月・進捗率・通期予想の修正履歴。過去の日を読むときは `earnings_day` の `disclosure_no` を渡す（省くと最新の開示）
  - `earnings_docs` / `earnings_doc_read` — `/api/stock/{code}/filings` と `/decks` の一覧、本文。短信の原文（`kind=filing`）と IR BANK の PDF 変換（`kind=pdf`）は HTML をテキストにして、説明資料（`kind=deck`）は読み取り済みのページ要約と表を返す。`offset` / `length` で区切る
  - `stock_overview` — `/api/stock/{code}/overview` と `/api/chart/{code}` と `/api/earnings-performance/{code}`。PER・PBR・利回り・ROE・自己資本比率・時価総額、次の決算予定、`today` 時点の騰落と 1 年高安からの距離、過去の決算後の株価
  - `market_context` — `/api/topix` の `date` 時点の騰落と直近の日足
  - `financial_statements` — `GET /api/stock/{code}/financials`。1銘柄の財務3表（`statement` = `BS`（既定）/ `PL` / `CF`）を、科目が行・決算期が列（古い → 新しい、見出しは `25.06`）の TSV で返す。百万円（1株当たりは円）。PL・CF は期首からの累計。2 列目は XBRL の要素名（`ext:` は会社独自、`pdf:` は短信 PDF にしか無い行）、最後の行が出どころ（`有報` / `XBRL` / `PDF`）。`periods` は 1-20（既定 8）、既定は連結で `consolidated=false` で個別。有報・短信 XBRL・短信 PDF の合わせ方は `docs/DESIGN_xbrl.md` §17

任意コマンドも restart もない。任意 SQL は `db_query` の読み取り専用 SELECT / WITH 1 文（先頭の `EXPLAIN` / `EXPLAIN QUERY PLAN` 可）だけ。月次ツールと決算ツールは FastAPI の `http://127.0.0.1:8000` だけを叩く（nginx は通らない。決算ツールは決まった GET だけ）。ログ系の行数は最大 500。`.env` の値は伏せる。
`fs_*` のプロセスユーザは `stock`。`stock-analyzer-mcp.service` の `LogsDirectory=app`（mode 0755）で `/var/log/app` の所有者は既に `stock` なので、ログ root を読むための追加 ACL や supplementary group は不要。`data/` は `WorkingDirectory` 配下。`LogsDirectoryMode` を後から絞るときは `stock` が `/var/log/app` を読める状態を保つ。
`monthly_get` の `values` は 1〜12。無い月は null。0 は実ゼロ。1年通して値のない行は出さない。
月次の unit は PDF 見出しから付ける（`DESIGN.md` §3 の表）。既存行の unit 変更は `PUT /api/v1/tables/{code}/{year}/elements`（`{element, unit}`）。行はその unit の正規名へ付け替わる。
月次の保存名は起動時に書き換えない（`docs/DESIGN_monthly_labels.md`「同一行・保存名の凍結」）。ずれた行があるとデプロイ（`jobs/migrate.py`）のログに `月次の保存名ドリフト: N 行` が出る。正規化や `config/monthly/issuer_aliases.json` を変えたあとは Actions の **Monthly names** を `plan`（既定、書かない）で流してログを見てから `apply`。中身は `jobs/monthly/monthly_names.py`。

認証は `MCP_TOKEN`（Bearer または OAuth で発行した access token）。GitHub secret と同値。値をログに出さない。

アプリと MCP のファイルログは `/var/log/app/`（`stock-analyzer.log` / `mcp.log`）。cron はジョブごと `/var/log/app/<stem>.log`（例: `tdnet_monthly_r2.log`、日次は `run_batch.log`）。`deploy/cron-run.sh` が標準出力と標準エラーをそこへ書き、各行の行頭に JST の `YYYY-MM-DD HH:MM:SS` を付ける。インタプリタは `.venv/bin/python3` を優先し、無ければ `.venv/bin/python`。どちらも無ければ `fail cron … exit=127` で終わる。python は `nice -n 10 ionice -c2 -n7` 経由で起動する（無いコマンドは外す。`ionice -c3` は使わない）。uvicorn と MCP の service は優先度を下げない。ジョブプロセスの TZ は変えない。crontab はリダイレクトしない。古い `/var/log/app/batch.log` には新しい行を書かず、logrotate で消えるまで残す。アプリ直下の古い `batch.log` は provision が消す（rotate 後の再コピーはしない）。journal も残る。
14 日で logrotate（`/var/log/app/*.log`、copytruncate）。nginx は従来どおり `/var/log/nginx/`。

fluent-bit は live ファイルだけを tail する（`stock-analyzer.log` / `mcp.log` / ジョブの `/var/log/app/*.log`、`/var/log/nginx/error.log`）。ジョブログの Slack 見出しの source はファイル名。`stock-analyzer.log` と `mcp.log` と古い `batch.log` はジョブ側の tail から外す。systemd は `stock-analyzer.service` と `stock-analyzer-mcp.service` と監視の `monitoring-*.service` の `Failed with result` / `Main process exited`。access.log と `html_ingest_ledger.json` と `material_stats_ledger.json` と rotate 済みの `.1` / `.gz` は見ない。batch は行頭の時刻のあとの `fail cron` と非ゼロの `failed=` と `errors=` を page する。行単位の `fail ` と batch の Traceback は page しない。Webhook が無いときはユニットを enable するだけで start しない。Slack は公式 slack 出力ではなく http + `fluent-bit_slack.lua`（JST と `#E01E5A` の赤帯）。`[OUTPUT]` はタグ別（`app` → #alerts-app、`batch` → #alerts-batch、`nginx` / `mcp` / `systemd` → #alerts-infra、`quiet` → #alerts-quiet、未分類 → #alerts）。nginx の `limiting connections by zone` と `connect() failed (111: Connection refused)` は rewrite_tag で `quiet` にする。`nginx-site.sh` の `limit_except GET { deny all; }`（`/monthly` と `/api/v1/tables`）に当たった POST / PUT / PATCH / DELETE / OPTIONS の `access forbidden by rule` は設計どおりの拒否なので grep の Exclude で落とし、どのチャンネルにも送らない（error.log には残る）。GET / HEAD・ほかのパスやメソッド・`auth request unexpected status`・upstream 失敗・`[crit]` / `[emerg]` は従来どおり #alerts-infra。URI は `slack.env` の `SLACK_WEBHOOK_URI` と `SLACK_WEBHOOK_URI_{APP,BATCH,INFRA,QUIET}`。新しい Webhook が未設定・空なら provision が従来の URI をコピーする（QUIET だけ INFRA へ）。secret 未登録のままデプロイしても #alerts / #alerts-infra に届く。アプリにログ API は足さない。

Actions の backfill は `cron-run.sh` を通さず、ワークフローのログに出す。VPS のジョブファイルには混ぜない。`cron-run.sh` を手で叩いたときだけ、cron と同じファイルに追記する。

リモート MCP（Grok / Cursor / Claude）は OAuth 前提。`https://kabukpi.com/mcp` に Bearer なしで来ると 401 と resource metadata を返す。Claude.ai のカスタムコネクタは Client ID を空のままで Dynamic Client Registration（`POST /oauth/register`）する。許可画面で `MCP_TOKEN` を入れる。

Grok:

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

Claude.ai（Customize → Connectors → Add custom connector）:

1. Server URL: `https://kabukpi.com/mcp`
2. Advanced の OAuth Client ID / Secret は空（DCR）。手で入れるなら Client ID は `stock-analyzer`、Secret は空
3. Connect → 許可画面で `MCP_TOKEN`

「kabukpi のサインインサービスに登録できませんでした」は DCR が `invalid_redirect_uri` のとき。Claude の callback（`https://claude.ai/api/mcp/auth_callback`）は許可リストに入っていること。Client ID を足しても authorize が同じ redirect を見るので、リスト外ならどちらも止まる。

Cursor / Grok Bot も同じ OAuth（PKCE、クライアントシークレットなし）。許可する redirect は:

- `https://www.cursor.com/agents/mcp/oauth/callback`
- デスクトップ: `http://localhost:8787/callback`（localhost で許可済）
- フォールバック: `cursor://anysphere.cursor-mcp/oauth/callback`
- Claude: `https://claude.ai/api/mcp/auth_callback` と `https://claude.com/api/mcp/auth_callback`

well-known は Access の外。`/.well-known/oauth-authorization-server` と `/.well-known/openid-configuration`（同じ AS メタデータ。`/mcp` 付きも含む）と `/oauth/` は MCP プロセスへプロキシする。
`/.well-known/` 全体は外さない（acme-challenge は従来どおり）。

MCP 自体が死んだときの退避路は Actions の **Debug VPS**。

## 監視（メトリクスとしきい値・KAB-333）

`deploy/monitoring/` を provision が入れる（`apply.sh` の `monitoring_apply`。失敗してもアプリの provision は続ける）。どれもユーザ `monitoring` で動き、`127.0.0.1` だけで待ち受ける。ufw も開けない。

| ユニット | 役割 | 待ち受け |
|---|---|---|
| `monitoring-victoria-metrics` | メトリクスの保存（`-retentionPeriod` で保持期間）。下の 4 つを 30 秒ごとに取りに行く（`scrape.yml`） | 8428 |
| `monitoring-vmalert` | `rules/*.yml` を評価して Alertmanager へ送る。状態は VictoriaMetrics に書くので再起動しても `for` の経過は消えない | 8880 |
| `monitoring-alertmanager` | Slack #alerts-infra へ送る（`alertmanager.yml`） | 9093 |
| `monitoring-node-exporter` | CPU・メモリ・ディスク・ネットワーク。FUSE（rclone / sshfs）と仮想 fs は見ない | 9100 |
| `monitoring-process-exporter` | プロセスのまとめ（`process-exporter.yml`。`app` / `mcp` / `job:<ジョブ名>` / nginx など / `other`） | 9256 |

- 実行ファイルは `binaries.txt`（版と sha256 を固定）を `fetch.sh` が GitHub Releases から落として `/opt/monitoring/bin` に入れる。上げるときは `binaries.txt` の行を差し替える（各リリースの checksum ファイルの値）
- 設定は `/etc/monitoring/`。デプロイのたびに repo と同じにし、中身が変わったサービスだけ restart する。止まっていれば start する
- しきい値は `rules/host.yml`。CPU とメモリは 80% / 90% が 5 分続いたら warning / critical、下がってから 5 分で解消。ディスクはすぐ。critical の間は同じものの warning を出さない（`inhibit_rules`）。解消は 1 回送る（`send_resolved`）。続いている間は送り直さない（`repeat_interval` と `--data.retention`）。node / process が 5 分取れないときは `MetricsTargetDown`
- Webhook は Secret を増やさない。provision が `slack.env` の `SLACK_WEBHOOK_URI_INFRA` から `/etc/monitoring/slack_infra_url`（640 root:monitoring）を作る。無いときは Alertmanager を起こさない
- 監視のユニット自体が落ちたときは fluent-bit の systemd 入力（`Failed with result` / `Main process exited`）が #alerts-infra に送る
- バッチ: `deploy/cron-run.sh` がジョブの終わりに `cron_job_duration_seconds` / `cron_job_exit_code` / `cron_job_last_run_timestamp_seconds`（ラベル `cron_job`）を `127.0.0.1:8428/api/v1/import/prometheus` に送る。送れなくてもジョブの結果とログは変わらない。`CRON_METRICS_URL=` で止める
- 記録ルール: `host:cpu_usage:percent` / `host:memory_usage:percent` / `host:disk_usage:percent`（df と同じ used / (used + avail)、ラベル `mountpoint`）
- AI からはログ MCP の `metrics_query`。手元から直接見るときは `ssh -L 8428:127.0.0.1:8428` してブラウザで `http://127.0.0.1:8428/vmui/`
- テスト: `tests/ops/test_monitoring.py`。ルールは `deploy/monitoring/tests/*_test.yml` を `vmalert-tool unittest` で、通知は実物の Alertmanager と偽の Slack で、バッチは実物の VictoriaMetrics で確かめる。実行ファイルは `MONITORING_BIN_DIR`（`bash deploy/monitoring/fetch.sh <dir>`）。無ければその分だけ skip。CI は先に落とす

## 共有ファイル（data/）と月次 / XBRL バッチ

ジョブが `--dest` を省略したときの保存先はリポジトリの `data/` である。接頭辞は `monthly`、`xbrl`、`inventory`、`stats`、`intraday`。`SHARE_ROOT` を置くとその直下になる。読むときは MCP `fs_*`（`root=data`）か box の `/mnt/vps/data`。R2 は使わない（KAB-66 以降。KPI 方針 2026-10-20）。

### 残置: rclone FUSE（`/home/stock/r2-share`）

VPS 上で rclone が bucket `share` を `/home/stock/r2-share` に user mount する（KAB-66 以前の保存先。ジョブの既定 dest ではない）。
ユーザ `stock`、`loginctl enable-linger stock` でログアウト・再起動後も残る。systemd ユニットは `deploy/r2-share.service`（秘密情報は含まない）。`Restart=always` / `RestartSec=5` で rclone プロセスが落ちたら付け直す。プロセスが残ったまま FUSE だけ切れる場合は `r2-share-watch.timer`（5 分ごと）が `/proc/mounts` と `ls` を見て `r2-share.service` を restart する。デプロイ（`provision.sh` → `r2-share-apply.sh`）はユニット / `rclone.conf` / watch の内容ハッシュが変わったときだけ daemon-reload し、**マウント unit か rclone.conf が変わったときだけ** `r2-share.service` を restart する。同じなら動いているマウントには触らない。ジョブ側の打ち切りは `DESIGN.md` §3。マウントと `r2-share-watch.timer` は残す。撤去は別 issue。

### data/ へのコピー

マージ後、10:30–12:00 JST（IR HTML ingest のあと、XBRL 12:00 の前）か、デプロイ直後で次の書き込みジョブの前に、ユーザ `stock` で一度だけ実行する。マウントと `r2-share-watch.timer` は上げたままにする。R2 のオブジェクトは消さない。dest が FUSE 上だと exit 2。中身が違う dest は `--force` なしでは触らず exit 1。不一致は `mismatch path=… src_size=… dest_size=… src_sha=… dest_sha=…` を 1 行ずつ出す（多いときは先頭 50 件と `mismatch_truncated`）。`--list-mismatches` はコピーせず一覧だけ。手動実行は `deploy/cron-run.sh jobs/ops/migrate_r2_share.py` なら MCP `batch_logs job=migrate_r2_share` で読める。定期 cron には載せない。

```bash
cd /home/stock/stock-analyzer
.venv/bin/python jobs/ops/migrate_r2_share.py
# または
deploy/cron-run.sh jobs/ops/migrate_r2_share.py --list-mismatches
```

終了コードが 0 なら、コピー対象の src は dest にあり sha256 が一致している。コピー対象の件数は `files_src` から `skipped_cache` を引いた数で、その数が `files_dest_copied_this_run` と `files_skipped_identical` の和と一致する。2 回目は `files_dest_copied_this_run=0` で終了コード 0。`xbrl/_cache` は既定ではコピーしない。

マウント上と `data/` の接頭辞:

- `monthly/{code}/YYYYMMDD_CODE_….pdf|xlsx|xls|html`（`{code}` は TDnet の発行体コードそのまま。9279 / 262A。J-Quants 5桁にはしない）
- `briefing/{code}/YYYYMMDD_CODE_….pdf`（決算説明資料。`{code}` は月次と同じ 4 桁。索引の code は 5 桁。`DESIGN.md`）
- `xbrl/{code4}/…zip` と `xbrl/_unmatched/`
- `inventory/vps-packages.txt`（上書き）と `inventory/history/vps-packages-YYYYMMDD.txt`
- `intraday/1m/YYYY/YYYY-MM-DD.parquet`（yfinance 1 分足。1 日 1 ファイル・全銘柄。書いた銘柄は上書きしない。`DESIGN.md` §3「分足」）
- `stats/meti/{seidou,sekiyu}/<経産省のファイル名>`（素材の月次量の Excel の控え。中身が変わった版は `_YYYYMMDD` つき。`DESIGN.md` §3）

初回設定:

1. GitHub secrets に `R2_ACCESS_KEY_ID` と `R2_SECRET_ACCESS_KEY` を入れる
2. Actions → CI/CD → Run workflow、`bump: none` で再デプロイする。
   `.env` の当該行と `/home/stock/.config/rclone/rclone.conf`（chmod 600）が書かれ、user unit `r2-share.service` が mount する。
   secrets が無いときは provision は落とさず、ユニットは enable のみ
3. 月次 PDF の初回バックフィルは Actions → **TDnet monthly backfill** → Run workflow、`days=30`。
   SSH で `jobs/monthly/tdnet_monthly_r2.py 30` が走る（通常デプロイでは走らない）

日足を全部取り直すとき（調整後株価のずれと UL / LL の空きを埋める。KAB-188）は、19:30 の日次バッチと重ならない時間にユーザ `stock` で一度だけ流す。止めても再実行で続きから。

```bash
deploy/cron-run.sh jobs/market/run_batch.py --refetch-prices   # ログは batch_logs job=run_batch
```

プランの期間より前の行の調整後株価を直すとき（KAB-193）は、同じく日次バッチと重ならない時間に一度だけ流す。API は呼ばない。

```bash
deploy/cron-run.sh jobs/market/run_batch.py --readjust-prices
```

TDnet が約 30 日で消すより前の決算説明資料（KAB-330）は、IR BANK の開示一覧から一度だけ手で取る。cron には入れない。毎日の分は 22:10 の `jobs/market/tdnet_briefing_r2.py`。429 / 503 で止まっても、次の実行は未了の銘柄から続く。PDF を書く直前に保存先の空きを見て、25GiB 未満なら `stopped=disk_low` で終了コード 0（その社は未了のまま。KAB-355）。同じ code の最新資料から 1 年以上前の PDF は手元に残さず、その会社の分だけ OneDrive へ送って索引は最初から `onedrive`。`--limit` は 1 回の社数。`--dry-run` は一覧だけ見て PDF も索引も書かない。`--rescan` は読み終えた社の一覧も読み直す（`--code` と `--all` のどちらとも。KAB-338）。索引にある PDF は取り直さない。ログは `deploy/cron-run.sh` なら `/var/log/app/irbank_ingest.log`（`batch_logs` の `job=irbank_ingest`）。

```bash
python tools/briefing/irbank_ingest.py --code 7203 --dry-run
deploy/cron-run.sh tools/briefing/irbank_ingest.py --all --limit 80 --sleep 1
```

保存した決算説明資料を Claude の Message Batches API で読む（KAB-349。`DESIGN.md`）のも、いまは手で回す。cron には入れない。`ANTHROPIC_API_KEY` が要る（従量課金）。`submit` で出し、1 時間ほどしてから `collect`（終わっていない Batch は次の `collect` で拾う。何度流してもよい）。`--dry-run` は PDF を読んで依頼の数を数えるだけ。ログは `/var/log/app/briefing_extract.log`（`batch_logs` の `job=briefing_extract`）。

```bash
python jobs/market/briefing_extract.py submit --code 6857 --dry-run
deploy/cron-run.sh jobs/market/briefing_extract.py submit --limit 50
deploy/cron-run.sh jobs/market/briefing_extract.py collect
python jobs/market/briefing_extract.py status
```

TDnet に説明資料を出さない会社の分（KAB-413）は `jobs/market/homepage_briefing.py` が会社の IR ページから取る。対象は `config/market/briefing_pages.json`。cron にはまだ入れていない。索引にある PDF は取り直さない。`--dry-run` は新しく保存する分を出すだけ。`--limit` は 1 社で新しく保存する件数（ページの上から）。

```bash
deploy/cron-run.sh jobs/market/homepage_briefing.py --only 6728 --dry-run
deploy/cron-run.sh jobs/market/homepage_briefing.py --only 6728 --limit 2
```

初回の一括（KAB-429）は直近 5 年に絞る。`--years 5` は 5 年前の今日より前の資料を保存しない。空きが足りなくなると `stopped=disk_low` で止まるので、翌日の `briefing_offsite`（1 年以上前の PDF を OneDrive へ）のあとに同じコマンドを流せば続きから入る。保存しても読み取り（`briefing_extract`）は走らない。ログは `/var/log/app/homepage_briefing.log`（`batch_logs` の `job=homepage_briefing`）。

```bash
deploy/cron-run.sh jobs/market/homepage_briefing.py --years 5 --dry-run
deploy/cron-run.sh jobs/market/homepage_briefing.py --years 5
```

決算短信の表紙の有無（`tanshin_covers`。KAB-416）は、初回だけ手で長めに入れる。どの会社も 1 四半期に 1 回は短信を出すので、`--days 100` で各社の最新の短信が入る。DB にある短信を読むだけで、外へは取りに行かない。2 回流しても同じ行を上書きする。

```bash
deploy/cron-run.sh jobs/market/tanshin_covers.py --days 100 --dry-run
deploy/cron-run.sh jobs/market/tanshin_covers.py --days 100
```

1 年以上前の決算説明資料（KAB-345、送り方は KAB-355）は `jobs/market/briefing_offsite.py` が OneDrive へ移す。会社ごとに最新の資料の日付から 1 年以上前の PDF だけ。対象はバッチごとに取り直し、`rclone copy --files-from` でまとめて送り、`check --one-way` で一致したものだけ索引を `onedrive` にしてから手元を消す。行は消さない。途中で落ちても再実行で続きから揃う。`--dry-run` は対象を出すだけ。`--limit` はその実行の件数。毎日 1 回 cron が動かす（KAB-353。01:40 JST）。同じジョブが既に動いているときは移さず終わる。1 件ずつ送っている古いプロセスが残っている間も、新しい実行は移さない。止めてから次を始める。手の全件移動が先に走っていても二重にはならない。ログは `deploy/cron-run.sh` なら `/var/log/app/briefing_offsite.log`（`batch_logs` の `job=briefing_offsite`）。

```bash
deploy/cron-run.sh jobs/market/briefing_offsite.py --dry-run
deploy/cron-run.sh jobs/market/briefing_offsite.py --limit 20
```

cron（JST）。`run_batch.py` は 19:30 の 1 回。

| スクリプト | cron |
|---|---|
| `jobs/monthly/tdnet_monthly_r2.py 7` | 18:00 / 21:00（直近7日。一覧は TDnet。取りこぼしの拾い直し） |
| `jobs/market/tdnet_briefing_r2.py 7` | 22:10（決算説明資料 → `data/briefing`。直近7日。既存 PDF は skip。索引行だけの補完は `reindexed`。初回の約30日は `deploy/cron-run.sh jobs/market/tdnet_briefing_r2.py 30` を手で一度。デプロイでは走らない） |
| `jobs/market/tanshin_covers.py --days 3` | 23:35（決算短信の表紙の「補足説明資料の有無」「説明会の有無」→ `tanshin_covers`。直近 3 日に短信を出した会社の最新 1 件） |
| `jobs/monthly/tdnet_monthly_pdf_sidecar.py` | 18:20 / 21:20（TDnet PDF 取得の後） |
| `jobs/monthly/tdnet_monthly_pdf_ingest.py` | 18:30 / 21:30（sidecar の後。ledger は `/var/log/app/pdf_ingest_ledger.json`） |
| `jobs/monthly/ir_monthly_r2.py` | 10:00 / 16:00 |
| `jobs/monthly/ir_monthly_html_r2.py` | 10:20 / 16:20 |
| `jobs/monthly/ir_monthly_html_ingest.py` | 10:30 / 16:30（HTML 取得の後。ledger は `/var/log/app/html_ingest_ledger.json`） |
| `jobs/monthly/ir_monthly_json_ingest.py` | 10:30 / 16:30（CloudFront JSON → loopback POST。7649） |
| `jobs/monthly/ir_monthly_xls_ingest.py --all --post` | 10:40 / 16:40（`ir_monthly_r2.py` が保存した最新の xls / xlsx → loopback POST。1878 / 7606） |
| `jobs/xbrl/tdnet_xbrl_r2.py 2` → `xbrl_parse.py` → `xbrl_normalize.py` → `xbrl_statements.py` | 12–23 時の毎時 00 分（1 行で順に。前日と当日。取得が失敗しても parse / normalize / statements は走らせる。単独やり直しは `deploy/cron-run.sh jobs/xbrl/xbrl_parse.py` など） |
| `jobs/market/pts_snapshot.py` | 平日 15:30〜24:00 の 30 分ごと（株探 PTS ランキング。24:00 は翌日 0:00 の回） |
| `jobs/market/intraday_1m.py` | 平日 20:30（yfinance 1 分足 → `data/intraday/1m/`。日次バッチの後。約 45 分） |
| `jobs/ops/vps_packages_r2.py` | 06:40 |
| `jobs/ops/backup_data.py` | 03:30（`data/xbrl` と `data/monthly` を OneDrive へ `rclone copy`。VPS 側は消さない。`data/edinet` は `rclone move`。edinet は 50 分で区切り、残りは翌日。[`docs/DESIGN_backup.md`](../docs/DESIGN_backup.md)） |
| `jobs/ops/backup_db.py` | 05:00（SQLite 整合バックアップ。OneDrive へ送れて確認できたら、VPS の今回の gzip は消す。手順は [`docs/DESIGN_backup.md`](../docs/DESIGN_backup.md)） |
| `jobs/market/briefing_offsite.py` | 01:40（1 年以上前の決算説明資料を OneDrive へ。既に同じジョブが動いていれば始めない） |

振る舞いは `DESIGN.md` §3 と `docs/DESIGN_xbrl.md`。`rclone.conf` と取得ファイルはリポジトリに置かない。

### VPS パッケージ一覧（セキュリティ照合）

読み取り専用。`.venv` の `pip freeze`、`dpkg-query -W`（全パッケージ。nginx / openssh-server / openssl / certbot / python3 / fluent-bit / fuse3 / ufw は必ず行に出す）、`uname -r`、`/etc/os-release`、`rclone version` をプレーンテキスト 1 ファイルにまとめる。先頭は JST の `generated-at` と hostname。無いコマンドは `not installed` で、ジョブ自体は落とさない。環境変数・鍵・`rclone.conf` は書かない。

保存先は `data/inventory/`。

- `data/inventory/vps-packages.txt`（毎回上書き）
- `data/inventory/history/vps-packages-YYYYMMDD.txt`（同日の再実行は上書き）

失敗時は他ジョブと同じく `deploy/cron-run.sh` が `fail cron … exit=` を `/var/log/app/vps_packages_r2.log` に書き、fluent-bit が page する。

デプロイ後に一度だけ手で走らせる（ユーザ `stock`）:

```bash
cd /home/stock/stock-analyzer && deploy/cron-run.sh jobs/ops/vps_packages_r2.py
```

### SQLite 日次バックアップ

WAL 中の `cp` は使わない。`jobs/ops/backup_db.py` が sqlite3 backup API → `PRAGMA quick_check` → gzip 圧縮レベル 1。ローカル成功後に `rclone copy` で `onedrive:stock-analyzer-backups/{daily,weekly}/` へ送り、`rclone check`（失敗時はサイズ）で確認する。世代数は OneDrive（日次 7・週次 4）。確認と OneDrive 側の世代整理が成功したら、今回の gzip は VPS から消す。失敗したときは今回のファイルを残し、`fail cron` が `#alerts-batch` に page する。それより前からあるローカルの世代は消さない。rclone / remote が無いときは警告してスキップ（今回のファイルも残す）。R2 には出さない。手順は [`docs/DESIGN_backup.md`](../docs/DESIGN_backup.md)。

`deploy/crontab` は `provision.sh` が `crontab -u stock` で入れる。デプロイ後に一度だけ手で走らせる（ユーザ `stock`）:

```bash
rclone listremotes
rclone lsd onedrive:stock-analyzer-backups || true
cd /home/stock/stock-analyzer && deploy/cron-run.sh jobs/ops/backup_db.py
ls -lh /home/stock/backups/daily /home/stock/backups/weekly
rclone ls onedrive:stock-analyzer-backups/daily
rclone ls onedrive:stock-analyzer-backups/weekly
tail -n 50 /var/log/app/backup_db.log
```

ログに `offsite ok daily` と `removed local daily=1 ... offsite=ok` があり、今回の `stock-YYYY-MM-DD.db.gz` は VPS に無く、OneDrive にあること。`fail backup_db:` なら今回の gzip は VPS に残る。`offsite skipped` なら送信していないので残る。デプロイ前からある世代はジョブでは消えない。空ける手順は [`docs/DESIGN_backup.md`](../docs/DESIGN_backup.md) の「既にある VPS の世代」。

### daily_prices を WITHOUT ROWID に作り直す（一度だけ・KAB-289）

rowid 表の `daily_prices` は日ごとに全銘柄を追記するので、1 銘柄の行が表全体に散り、チャートなど銘柄ごとの読み出しがページのランダム読みになる。`WITHOUT ROWID` は行を (code, date) 順に置く。新しい DB はモデルのとおり最初から `WITHOUT ROWID`。本番の既存 DB は `jobs/ops/rebuild_daily_prices.py` を一度だけ手で流す。デプロイの `migrate.py` は作り直さない。アプリはどちらの形でも動く。

- 作り直しは 1 トランザクション。コピーの間は書き込みロックを握るので、他のライター（cron のバッチ・月次 PUT）は `busy_timeout` のあと失敗しうる。読み取り（API）は WAL なので止まらない
- 流す時間: 書き込みジョブの少ない夜中。00:15 の `edinet_archive` が終わったあと（00:30 ごろ）に始める。19:30 の日次バッチと重ねない
- 先に `jobs/ops/backup_db.py` でバックアップを取る
- ディスクは表の約 3 倍（新しい表と WAL）を一時的に使う。足りなければジョブが止まる（引数なしで見積もりだけ出る）
- DB ファイルは縮まない（空いたページは次の書き込みで使われる）

```bash
cd /home/stock/stock-analyzer
deploy/cron-run.sh jobs/ops/backup_db.py
.venv/bin/python jobs/ops/rebuild_daily_prices.py          # 見積もりだけ
deploy/cron-run.sh jobs/ops/rebuild_daily_prices.py --yes   # 作り直す。MCP batch_logs job=rebuild_daily_prices で読める
```

2 回目は「既に WITHOUT ROWID です」で何もしない。

### TDnet 月次 PDF の未投入レポート（読み取り専用）

前回と同じ書式の PDF は次の節の cron が入れる。残りは MCP `monthly_ingest` の手作業。`data/monthly` の PDF と `monthly_values` の差は、VPS 上のスクリプトで見る（MCP ツールは足さない。cron にも載せない）。

ユーザ `stock` で。SQLite は `mode=ro`。dest にも DB にも書かない。

```bash
cd /home/stock/stock-analyzer
.venv/bin/python tools/monthly/tdnet_monthly_pending.py
.venv/bin/python tools/monthly/tdnet_monthly_pending.py --only-stale
.venv/bin/python tools/monthly/tdnet_monthly_pending.py --code 9279 --json
```

既定の dest は `jobs/monthly/tdnet_monthly_r2.py` と同じ `data/monthly`。DB は `DATABASE_URL`（無ければリポジトリの `stock.db`）。上書きは `--dest` と `--db`。

列:

| 列 | 意味 |
|---|---|
| `code` | TDnet 発行体コード（4桁。9279 / 262A） |
| `pdf_date` | その発行体で一番新しい PDF ファイル名の `YYYYMMDD` 接頭辞（TDnet pubdate / ingest の `as_of`） |
| `expected` | その開示日がカバーするとみなす暦月 |
| `db_latest` | `monthly_values` に値がある最大の `(year, month)`。行が無ければ `-` |
| `gap` | `expected - db_latest` の月数。正なら DB が遅れている。行が無ければ `-` |
| `status` | `ok` / `STALE` / `MISSING`。`STALE` と `MISSING` の行頭は `*` |
| `pdf` | 最新 PDF のファイル名 |

ヒューリスティック: ファイル名の日付は開示日。本文を開かず `fiscal_year_end` も見ないので、ingest の列→暦年と完全には一致しない。通常の月次売上は翌月開示（`2026-08-05` → 7月度）なので、**expected は開示日の前暦月**。これは ingest の `_anchor_year(last_month, as_of)` に「右端列 = 前暦月」を渡した値と同じ。21:00 の TDnet バッチ分は古くても残る（新しさでは落とさない）。`--only-stale` は `STALE` と `MISSING` だけ。HTML / xlsx / xls は見ない（同じ `monthly/{code}/` の PDF だけ）。`PENDING_IGNORE_CODES`（いまは 7062 のみ。月次開示終了・表なし）は MISSING に出さない。`MONTHLY_SKIP_CODES` とは別。

### TDnet 月次 PDF の自動投入（前回と同じ書式だけ）

2 段。どちらも cron（上の表）。振る舞いの正本は `DESIGN.md` §3。

1. `jobs/monthly/tdnet_monthly_pdf_sidecar.py` が、DB の最新月より新しい PDF を持つ銘柄だけ、DB に入った前回の PDF から「どの行が DB のどの行か」を学び、同じ書式なら PDF の隣に `….pdf-ingest.json` を書く。POST しない
2. `jobs/monthly/tdnet_monthly_pdf_ingest.py` が code ごとに最新の `.pdf-ingest.json` を loopback へ POST する。先に `dry_run` して、サーバが決めた列の暦年が sidecar の `columns` と違えば POST しない。sha256 ledger で同じ sidecar は二度送らない

書けなかった銘柄は `/var/log/app/tdnet_monthly_pdf_sidecar.log` に `manual <code> <pdf>: <理由>` が出る（page しない）。理由は次のどれか。今までどおり MCP `monthly_ingest` で入れる。

| 理由 | 意味 |
|---|---|
| `表N の見出し …` / `表の数 …` / `表N の行 …` | 前回と書式が違う（列・表・行名が変わった） |
| `新しい行 […]` | 前回無かった行・表に今月の値がある（新業態・新セグメント。見出しと行名が既存と同じブロック追加も含む） |
| `再現できない行: …` | 前回の PDF から DB の行を読み直せない（表でない本文、画像 PDF、DB に手で足した行など） |
| `…: DB と N か月食い違う` / `… が直近と桁違い` | 読んだ値が DB の既存月や直近の桁と合わない |
| `DB に入った前回の PDF が無い` | 初回、または前回分も未投入 |

手で回すとき（ユーザ `stock`）:

```bash
cd /home/stock/stock-analyzer
.venv/bin/python -u jobs/monthly/tdnet_monthly_pdf_sidecar.py --dry-run
.venv/bin/python -u jobs/monthly/tdnet_monthly_pdf_sidecar.py --only 428A
.venv/bin/python -u jobs/monthly/tdnet_monthly_pdf_ingest.py --ledger /var/log/app/pdf_ingest_ledger.json --dry-run
.venv/bin/python -u jobs/monthly/tdnet_monthly_pdf_ingest.py --ledger /var/log/app/pdf_ingest_ledger.json
```

`--force --only` / `--months` は `ir_monthly_html_ingest.py` と同じ。409（`UNIT_CONFLICT` など）で止まったら確かめてから `--force --only <code>`。自動で入ったものを直すときは MCP の `monthly_put_values`（暦年）。

### クラウドのエージェントから VPS を触る

経路は2つ。任意シェルは MCP に足さない。

| やりたいこと | 経路 |
|---|---|
| ログ・DB 件数・月次 ingest / 訂正 | `stock-analyzer` MCP（`https://kabukpi.com/mcp`）。ベンダーごとにコネクタ / MCP 設定で足す |
| `--reparse` や `ir_monthly_html_ingest.py` などシェル | Secrets / 環境変数の `VPS_SSH_KEY` → `ssh stock-vps`。無ければ人がローカル PC から SSH |

`VPS_SSH_KEY` は GitHub Actions と同じデプロイ秘密鍵でよい（`stock` の `authorized_keys` に載っているもの）。チャットに貼らない。

1. エージェントの Secrets / 環境変数に `VPS_SSH_KEY` を登録する（Cursor は Dashboard → Cloud Agents → Secrets のリポジトリ scope。Claude Code on the web は環境設定の環境変数。他のベンダーも同じ名前で渡す）
2. 新しいエージェントを起動する（既存 VM / コンテナには入らない）。`bash .cursor/install.sh` が `.cursor/setup-vps-ssh.sh` を呼ぶ。Cursor 以外で install を回さないときは `bash .cursor/setup-vps-ssh.sh` だけでよい
3. エージェントから:

```bash
ssh stock-vps 'cd /home/stock/stock-analyzer && .venv/bin/python -u jobs/monthly/ir_monthly_html_r2.py --reparse'
ssh stock-vps 'cd /home/stock/stock-analyzer && .venv/bin/python -u jobs/monthly/ir_monthly_html_ingest.py --ledger /var/log/app/html_ingest_ledger.json'
```

接続確認: `ssh stock-vps 'echo ok $(hostname)'`。失敗したら公開鍵が `stock` の `authorized_keys` にあるか、Actions の **Debug VPS** を見る。

### IR 月次 HTML の強制再取得・再投入（--force）

パーサーを直したあと、中身が同じ HTML を今日の日付で書き直し、sidecar を今のパーサーで作り直してから入れ直す。cron のコマンドは変えない。銘柄を指定して手元で走らせる。

cron の ingest は行に `force` を付けない。正規化後に同じ行名で保存 unit が違う行（`UNIT_CONFLICT`）、空白・大小だけ違う既存行（`ELEMENT_SIMILAR_EXISTS`）、`strip_yoy_fragments` 後の名前が同じで yoy と number だけ違う既存行が今回の rows に無いとき（`YOY_NUMBER_CONFLICT`）は 409 で止まり、ledger にも書かない。同じ POST に実数と前年比の両方があるときは `YOY_NUMBER_CONFLICT` にならない。ログの existing / incoming を確かめてから、下の `--force --only` で入れる。ingest の `--force` は ledger を無視し、行にも `force` を付ける。

順番は fetch、その次に ingest。どちらも `--force` には `--only` が要る。`--only` は `3082` でも J-Quants の `30820` でもよい（4桁に切る）。

ingest の `--months` は暦年の `YYYY-MM`（カンマ区切り、繰り返し可）。sidecar の months と rows はそのまま送り、`only_months` でサーバに絞らせる。年割当は ingest の mapping。probe はしない。`--months` の部分投入は ledger を更新しない。同じ sha の残りの月を、次の通常実行が飛ばさないため。`--force` だけで sidecar 全体を送ったときは、通常どおり ledger に書く。

HTML に無いセル（行自体がページに無い、など）は消さない。値がキーとして sidecar に無いセルは送らないので、サーバは既存の値を触らない。消すときは API で別途削除する。

ユーザ `stock` で:

```bash
cd /home/stock/stock-analyzer && .venv/bin/python -u jobs/monthly/ir_monthly_html_r2.py --force --only 3082
cd /home/stock/stock-analyzer && .venv/bin/python -u jobs/monthly/ir_monthly_html_ingest.py --ledger /var/log/app/html_ingest_ledger.json --force --only 3082 --months 2026-07,2026-08
```

### クラウドのエージェントから読む

共有ファイル（月次 PDF/HTML、XBRL ZIP、inventory、stats）は VPS の `data/` にある。R2 は使わない。読む経路は次の 2 つ。

1. MCP `fs_list` / `fs_read` / `fs_grep`（`root=data`）。`path` は `data/` からの相対（例 `monthly/9279/`、`xbrl/6036/`）。`fs_read` は 1 回あたり `MCP_FS_MAX_BYTES`（既定 1MiB）
2. box の読み取り専用 sshfs: `/mnt/vps/data`（boxro の chroot `/data` = `$APP_DIR/data`）

```bash
# MCP（stock-analyzer）: fs_list / fs_read / fs_grep  root=data  path=monthly/9279
pdftotext -layout /mnt/vps/data/monthly/9279/<file>.pdf -
```

- リポジトリに PDF / ZIP を置かない（`*.pdf` は commit 禁止）
- 書き込みはしない。新しいファイルは VPS の cron が `data/` に書く
- `/home/stock/r2-share` と rclone `r2:share` は読まない（MCP は r2-share を二重拒否）
