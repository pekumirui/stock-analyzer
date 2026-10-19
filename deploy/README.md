# VPS (ConoHa / Ubuntu 24.04)

手で nginx / ufw / systemd を積む必要はない。**root でデプロイ鍵を一度入れる**と、あとは GitHub Actions が冪等に初期設定する。

`deploy/provision.sh` が apt / nginx / ufw / certbot / systemd / crontab を冪等に揃える。
nginx の設定の正本は `deploy/nginx-site.sh`（`provision.sh` が source して `/etc/nginx` に書く）。CI の `tests/ops/test_nginx_public.py` が同じものを組み立てて `nginx -t` に通し、起動して公開 / Basic 認証 / MCP の振り分けを確かめる。

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
| `ESTAT_APP_ID` | 任意 | e-Stat API のアプリケーション ID（サプライタブの月次量）。あるときだけ `.env` の当該行を更新。無ければ `jobs/supply/material_stats.py --source estat` は何もしない（経産省 Excel はキー不要） |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | 推奨 | Cloudflare R2 (bucket `share`) の rclone 認証。あるときだけ `.env` と `~/.config/rclone/rclone.conf` を更新。無ければ既存を触らない |
| `SLACK_WEBHOOK_URL` | 推奨 | Slack Incoming Webhook（#alerts）。コミットしない。漏れたら rotate。provision が `/etc/fluent-bit/slack.env` に URL と `SLACK_WEBHOOK_URI` を書く。`.env` には入れない |
| `SLACK_WEBHOOK_URL_APP` | 任意 | Incoming Webhook（#alerts-app、fluent-bit タグ `app`）。未設定・空なら `SLACK_WEBHOOK_URL`（#alerts）にフォールバック。`.env` には入れない |
| `SLACK_WEBHOOK_URL_BATCH` | 任意 | Incoming Webhook（#alerts-batch、タグ `batch`）。未設定・空なら #alerts にフォールバック |
| `SLACK_WEBHOOK_URL_INFRA` | 任意 | Incoming Webhook（#alerts-infra、タグ `nginx` / `mcp` / `systemd`）。未設定・空なら #alerts にフォールバック |
| `SLACK_WEBHOOK_URL_QUIET` | 任意 | Incoming Webhook（#alerts-quiet、タグ `quiet`。nginx の `limiting connections by zone` と再起動中の `connect() failed (111: Connection refused)`）。未設定・空なら `SLACK_WEBHOOK_URI_INFRA`（#alerts-infra）にフォールバック。`.env` には入れない |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | 推奨 | nginx Basic 認証。無ければ既存 `htpasswd` を残す |
| `MCP_TOKEN` | 推奨 | ログ MCP の Bearer トークン。`openssl rand -hex 32`。無ければ VPS 上で生成（値は CI に出ない） |
| `VPS_DOMAIN` | 任意 | 未設定なら `160.251.179.50.sslip.io` |
| `VPS_HOST` / `VPS_USER` / `VPS_PORT` | 任意 | 未設定なら `160.251.179.50` / `stock` / `22` |
| `CERTBOT_EMAIL` | 任意 | 無いと certbot は `--register-unsafely-without-email` |

`stock.db` はパイプラインが触らない。初回データは手元で backfill して scp するか、VPS 上で `jobs/market/run_batch.py`。

## パイプラインが毎回やること

`update.sh`:

1. GitHub SSH（deploy key）を冪等に揃えてから `git pull --ff-only origin main`
2. `JQUANTS_API_KEY` / `ESTAT_APP_ID` / `MCP_TOKEN` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` があれば `.env` のその行だけ upsert。R2 の両方が揃っていれば `~/.config/rclone/rclone.conf` も書く。`SLACK_WEBHOOK_URL` と `SLACK_WEBHOOK_URL_{APP,BATCH,INFRA,QUIET}` は `.env` に書かない
3. `sudo provision.sh --sudoers-only`（`APP_DIR` だけ preserve。live sudoers を repo に合わせる）→ `sudo provision.sh`（パッケージ、systemd、nginx、ufw 22/80/443、Let's Encrypt、crontab、sudoers、R2 user mount、fluent-bit。Slack webhook は `--preserve-env`）。Webhook が空なら fluent-bit は enable のみで start しない
4. venv / `pip install` / `migrate.py`（`database is locked` は最大約 6 分まで再試行。19:30 の日次バッチや毎時 `xbrl_normalize` と重なるため）
5. `systemctl restart stock-analyzer` と `stock-analyzer-mcp`、localhost:8000 / :8010 のヘルスチェック

証明書の更新は `certbot.timer`（1日2回）。更新後は `renewal-hooks/deploy` で nginx を reload する。

ブラウザは [https://kabukpi.com/monthly](https://kabukpi.com/monthly) が公開の月次。個人ツールは [https://kabukpi.com/app](https://kabukpi.com/app)（Basic 認証）。証明書は RSA + ISRG Root X1。ECDSA 既定の Root YE は端末にまだ無く「セキュリティ保護なし」になる。

公開 location は `/monthly`・GET `/api/v1/tables`（code/year まで）・`/static/`・`robots.txt` / `sitemap.xml`。書き込み API と `/app` は Basic。公開側に `limit_req` は付けない。

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

## box からの読み取り専用 SFTP（boxro）

box エージェントはユーザ `boxro` で sftp するだけ（シェルもポート転送も不可）。chroot は `/srv/boxro`。`provision.sh` が冪等に揃える。

見えるもの:

| chroot 内 | 実体 |
|---|---|
| `/data` | `$APP_DIR/data`（`xbrl_golden` / `xbrl_taxonomy`） |
| `/logs` | `/var/log/app` |

見せない: `stock.db`、`.env`、`/home/stock/backups`、R2 FUSE（`/home/stock/r2-share`）。`data/` は既に bind しているので、`data/monthly` と `data/xbrl` は boxro から見える。`/home/stock/r2-share` は bind しない。

公開鍵は `deploy/ssh/boxro_authorized_keys`（1行1鍵、先頭 `restrict`）。provision が `/etc/ssh/authorized_keys/boxro`（root:root、644）へ同期する。鍵を足すときはそのファイルに行を足してデプロイ。`/etc/ssh/sshd_config.d/90-boxro.conf` が変わったときだけ `sshd -t && systemctl reload ssh`。`sshd -t` が失敗したら drop-in を戻す。restart はしない。boxro の失敗は stderr に出すが、アプリの provision（systemd / nginx / crontab）は続ける。既存の stock / root / デプロイ鍵には触らない。

元に戻す（root）:

```bash
rm -f /etc/ssh/sshd_config.d/90-boxro.conf /etc/ssh/authorized_keys/boxro
sshd -t && systemctl reload ssh
umount /srv/boxro/data /srv/boxro/logs
# /etc/fstab の /srv/boxro/data と /srv/boxro/logs の 2 行を消す
userdel boxro
rmdir /srv/boxro/data /srv/boxro/logs /srv/boxro
```

失敗したデプロイのやり直しは Actions の **Run workflow**（`bump: none`）。

## ログ MCP（Grok）

VPS 上で `mcp/log_mcp.py` が `127.0.0.1:8010` を待ち、nginx が Basic 認証なしで `/mcp` だけプロキシする。ポート 8010 は外に開けない。

ツール:

- `service_status` — プロセスと localhost:80 / :8000 の HTTP コード。MCP 自身（:8010）は自己 curl せず、この応答自体を ok とする
- `app_logs` — `/var/log/app/stock-analyzer.log` と journalctl。uvicorn は `deploy/uvicorn-log.json` で行頭に JST `YYYY-MM-DD HH:MM:SS` を付ける。`since` / `until` は `batch_logs` と同じ形。省略時は journal が直近 1h、ファイルは live の末尾だけ。指定すると `.1` / `.gz` も読み、行頭の時刻で絞る。時刻の無い行（Traceback の続き）は直前の行に付く
- `batch_logs` — `/var/log/app/<ジョブ>.log`。`job` は crontab の stem（`run_batch` / `pts_snapshot` / `fx_rates` / `tdnet_monthly_r2` / `tdnet_monthly_pdf_sidecar` / `tdnet_monthly_pdf_ingest` / `ir_monthly_r2` / `ir_monthly_html_r2` / `ir_monthly_html_ingest` / `ir_monthly_json_ingest` / `tdnet_xbrl_r2` / `xbrl_parse` / `xbrl_normalize` / `vps_packages_r2` / `backup_db`）。一覧に無い名前とパスは拒否。省略時は `stock-analyzer.log` と `mcp.log` 以外の live ファイル（古い `batch.log` を含む）。`since` / `until` は `1h` / `30m` / `today` / `yesterday` / `YYYY-MM-DD`。`until` はその時刻を含まない。昨日は `since=yesterday` と `until=today`。日時を指定したときは `.1` と `.gz` も読み、行頭の JST `YYYY-MM-DD HH:MM:SS` で絞る。`lines` は 1-500。超えた分は末尾だけ返すので範囲を狭める
- `nginx_logs` — nginx journal と error.log と `/var/log/nginx/stock-analyzer.access.log`。アクセスログの `rt=` は nginx 全体、`urt=` は uvicorn の処理秒。遅いリクエストは `grep` に `rt=[1-9]` などを渡す。`since` / `until` は `batch_logs` と同じ形。省略時は journal が直近 1h、ファイルは live の末尾だけ。指定すると logrotate 済みの `.1` / `.gz`（既定で 14 日分）も読み、行の時刻（JST）で絞る。URL の OAuth 一時値（`code` / `state` など）は伏せる
- `fs_list` / `fs_read` / `fs_grep` — 読み取り専用。`root` は `data`（リポジトリの `data/`、`MCP_DATA_DIR`）か `logs`（`/var/log/app`、`MCP_LOG_DIR`）。それ以外・絶対パス・`..`・symlink で root の外へ出るものは拒否。`.env*` / `*.db` / `*.sqlite*` / `stock.db` / `*.pem` / `id_*` / `*.key` / `rclone.conf` と `/home/stock/backups` / `/home/stock/r2-share` は二重に拒否。`fs_read` は 1 回あたり `MCP_FS_MAX_BYTES`（既定 1MiB）、切り詰め時は `truncated` とファイル全体の `size` を返す。認証は他ツールと同じ `MCP_TOKEN` / OAuth
- `db_stats` — stock.db の件数・日付範囲・デモ混在
- `db_lookup` — 1銘柄のマスタ・直近株価・決算・指標
- `fx_rate` — 指定日の為替。EUR ベースのクロス（例 USD/JPY）。休日・ECB非公表日は直近営業日（`as_of`）。`holiday_fallback` で明示
- `fx_rates` — 期間の為替（公表日のみ）。休日を直近営業日で埋めたいときは `fx_rate`
- `monthly_list` — 月次表一覧。localhost:8000 の GET /api/v1/tables
- `monthly_get` — 銘柄×年度の月次表。values は 1〜12。無い月は null
- `monthly_add_element` — 要素登録
- `monthly_put_values` — セル一括 upsert（null で削除）。year は暦年。訂正用
- `monthly_delete_element` — 値の無い行を消す。値が残っていれば 409。表は残す
- `monthly_get_doc` / `monthly_put_doc` — 年の表を 1 枚の文書で読んで、直して丸ごと返す。行名はそのまま（正規化・エイリアスなし）。改名は `was`、文書に無い行は削除。構造の変更は `allow_structure_change`、`dry_run` で差分だけ。仕様は `DESIGN.md` の API 表
- `monthly_set_industry` — 月次業種。PUT /api/v1/tables/{code}/industry。銘柄マスタの業種とは別
- `monthly_ingest` — PDF 月次の新規登録。POST /api/v1/tables/{code}/ingest。year は渡さない。
  列順 + as_of 時点の最新 `fiscal_year_end` から暦年を決める。月が重複する year-wrap は列順配列の `values`。`dry_run: true` は書かずに年割当と書くはずのセルを返す。`only_months` は `YYYY-MM` で列を絞る

任意コマンドも restart も任意 SQL もない。月次ツールは FastAPI の `http://127.0.0.1:8000` だけを叩く（nginx Basic は使わない）。行数は最大 500。`.env` の値は伏せる。
`fs_*` のプロセスユーザは `stock`。`stock-analyzer-mcp.service` の `LogsDirectory=app`（mode 0755）で `/var/log/app` の所有者は既に `stock` なので、ログ root を読むための追加 ACL や supplementary group は不要。`data/` は `WorkingDirectory` 配下。`LogsDirectoryMode` を後から絞るときは `stock` が `/var/log/app` を読める状態を保つ。
`monthly_get` の `values` は 1〜12。無い月は null。0 は実ゼロ。1年通して値のない行は出さない。
月次の unit は PDF 見出しから付ける（`DESIGN.md` §3 の表）。既存行の unit 変更は `PUT /api/v1/tables/{code}/{year}/elements`（`{element, unit}`）。行はその unit の正規名へ付け替わる。
月次の保存名は起動時に書き換えない（`docs/DESIGN_monthly_labels.md`「同一行・保存名の凍結」）。ずれた行があるとデプロイ（`jobs/migrate.py`）のログに `月次の保存名ドリフト: N 行` が出る。正規化や `config/monthly/issuer_aliases.json` を変えたあとは Actions の **Monthly names** を `plan`（既定、書かない）で流してログを見てから `apply`。中身は `jobs/monthly/monthly_names.py`。

認証は `MCP_TOKEN`（Bearer または OAuth で発行した access token）。GitHub secret と同値。値をログに出さない。

アプリと MCP のファイルログは `/var/log/app/`（`stock-analyzer.log` / `mcp.log`）。cron はジョブごと `/var/log/app/<stem>.log`（例: `tdnet_monthly_r2.log`、日次は `run_batch.log`）。`deploy/cron-run.sh` が標準出力と標準エラーをそこへ書き、各行の行頭に JST の `YYYY-MM-DD HH:MM:SS` を付ける。インタプリタは `.venv/bin/python3` を優先し、無ければ `.venv/bin/python`。どちらも無ければ `fail cron … exit=127` で終わる。ジョブプロセスの TZ は変えない。crontab はリダイレクトしない。古い `/var/log/app/batch.log` には新しい行を書かず、logrotate で消えるまで残す。アプリ直下の古い `batch.log` は provision が消す（rotate 後の再コピーはしない）。journal も残る。
14 日で logrotate（`/var/log/app/*.log`、copytruncate）。nginx は従来どおり `/var/log/nginx/`。

fluent-bit は live ファイルだけを tail する（`stock-analyzer.log` / `mcp.log` / ジョブの `/var/log/app/*.log`、`/var/log/nginx/error.log`）。ジョブログの Slack 見出しの source はファイル名。`stock-analyzer.log` と `mcp.log` と古い `batch.log` はジョブ側の tail から外す。systemd は `stock-analyzer.service` と `stock-analyzer-mcp.service` の `Failed with result` / `Main process exited`。access.log と `html_ingest_ledger.json` と `material_stats_ledger.json` と rotate 済みの `.1` / `.gz` は見ない。batch は行頭の時刻のあとの `fail cron` と非ゼロの `failed=` と `errors=` を page する。行単位の `fail ` と batch の Traceback は page しない。Webhook が無いときはユニットを enable するだけで start しない。Slack は公式 slack 出力ではなく http + `fluent-bit_slack.lua`（JST と `#E01E5A` の赤帯）。`[OUTPUT]` はタグ別（`app` → #alerts-app、`batch` → #alerts-batch、`nginx` / `mcp` / `systemd` → #alerts-infra、`quiet` → #alerts-quiet、未分類 → #alerts）。nginx の `limiting connections by zone` と `connect() failed (111: Connection refused)` は rewrite_tag で `quiet` にする。URI は `slack.env` の `SLACK_WEBHOOK_URI` と `SLACK_WEBHOOK_URI_{APP,BATCH,INFRA,QUIET}`。新しい Webhook が未設定・空なら provision が従来の URI をコピーする（QUIET だけ INFRA へ）。secret 未登録のままデプロイしても #alerts / #alerts-infra に届く。アプリにログ API は足さない。

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

well-known は Basic 認証の外。`/.well-known/oauth-authorization-server` と `/.well-known/openid-configuration`（同じ AS メタデータ。`/mcp` 付きも含む）と `/oauth/` は MCP プロセスへプロキシする。
`/.well-known/` 全体は外さない（acme-challenge は従来どおり）。

MCP 自体が死んだときの退避路は Actions の **Debug VPS**。

## Cloudflare R2 mount と月次 / XBRL バッチ

VPS 上で rclone が bucket `share` を `/home/stock/r2-share` に user mount する。
ユーザ `stock`、`loginctl enable-linger stock` でログアウト・再起動後も残る。systemd ユニットは `deploy/r2-share.service`（秘密情報は含まない）。`Restart=always` / `RestartSec=5` で rclone プロセスが落ちたら付け直す。プロセスが残ったまま FUSE だけ切れる場合は `r2-share-watch.timer`（5 分ごと）が `/proc/mounts` と `ls` を見て `r2-share.service` を restart する。デプロイ（`provision.sh` → `r2-share-apply.sh`）はユニット / `rclone.conf` / watch の内容ハッシュが変わったときだけ daemon-reload し、**マウント unit か rclone.conf が変わったときだけ** `r2-share.service` を restart する。同じなら動いているマウントには触らない。ジョブ側の打ち切りは `DESIGN.md` §3。

ジョブが `--dest` を省略したときの保存先はリポジトリの `data/` である。接頭辞はバケットと同じで、`monthly`、`xbrl`、`inventory`、`stats`。`SHARE_ROOT` を置くとその直下になる。マウントと `r2-share-watch.timer` は切り替えのあいだ残す。

### data/ へのコピー

マージ後、ユーザ `stock` で一度だけ実行する。マウントは上げたままにする。R2 のオブジェクトは消さない。

```bash
cd /home/stock/stock-analyzer
.venv/bin/python jobs/ops/migrate_r2_share.py
```

終了コードが 0 なら、コピー対象の src は dest にあり sha256 が一致している。コピー対象の件数は `files_src` から `skipped_cache` を引いた数で、その数が `files_dest_copied_this_run` と `files_skipped_identical` の和と一致する。2 回目は `files_dest_copied_this_run=0` で終了コード 0。`xbrl/_cache` は既定ではコピーしない。

マウント上と `data/` の接頭辞:

- `monthly/{code}/YYYYMMDD_CODE_….pdf|xlsx|xls|html`（`{code}` は TDnet の発行体コードそのまま。9279 / 262A。J-Quants 5桁にはしない）
- `xbrl/{code4}/…zip` と `xbrl/_unmatched/`
- `inventory/vps-packages.txt`（上書き）と `inventory/history/vps-packages-YYYYMMDD.txt`
- `stats/meti/{seidou,sekiyu}/<経産省のファイル名>`（素材の月次量の Excel の控え。中身が変わった版は `_YYYYMMDD` つき。`DESIGN.md` §3）

初回設定:

1. GitHub secrets に `R2_ACCESS_KEY_ID` と `R2_SECRET_ACCESS_KEY` を入れる
2. Actions → CI/CD → Run workflow、`bump: none` で再デプロイする。
   `.env` の当該行と `/home/stock/.config/rclone/rclone.conf`（chmod 600）が書かれ、user unit `r2-share.service` が mount する。
   secrets が無いときは provision は落とさず、ユニットは enable のみ
3. 月次 PDF の初回バックフィルは Actions → **TDnet monthly backfill** → Run workflow、`days=30`。
   SSH で `jobs/monthly/tdnet_monthly_r2.py 30` が走る（通常デプロイでは走らない）

cron（JST）。`run_batch.py` は 19:30 の 1 回。

| スクリプト | cron |
|---|---|
| `jobs/monthly/tdnet_monthly_r2.py 1` | 18:00 / 21:00 |
| `jobs/monthly/tdnet_monthly_pdf_sidecar.py` | 18:20 / 21:20（TDnet PDF 取得の後） |
| `jobs/monthly/tdnet_monthly_pdf_ingest.py` | 18:30 / 21:30（sidecar の後。ledger は `/var/log/app/pdf_ingest_ledger.json`） |
| `jobs/monthly/ir_monthly_r2.py` | 10:00 / 16:00 |
| `jobs/monthly/ir_monthly_html_r2.py` | 10:20 / 16:20 |
| `jobs/monthly/ir_monthly_html_ingest.py` | 10:30 / 16:30（HTML 取得の後。ledger は `/var/log/app/html_ingest_ledger.json`） |
| `jobs/monthly/ir_monthly_json_ingest.py` | 10:30 / 16:30（CloudFront JSON → loopback POST。7649） |
| `jobs/xbrl/tdnet_xbrl_r2.py 1` | 12–23 時の毎時 00 分 |
| `jobs/xbrl/xbrl_parse.py` / `jobs/xbrl/xbrl_normalize.py` | 12–23 時の毎時 10 分 / 20 分 |
| `jobs/market/pts_snapshot.py` | 平日 15:30〜24:00 の 30 分ごと（株探 PTS ランキング。24:00 は翌日 0:00 の回） |
| `jobs/ops/vps_packages_r2.py` | 06:40 |
| `jobs/ops/backup_db.py` | 05:00（SQLite 整合バックアップ。R2 には出ない。手順は [`docs/DESIGN_backup.md`](../docs/DESIGN_backup.md)） |

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

WAL 中の `cp` は使わない。`jobs/ops/backup_db.py` が sqlite3 backup API → `PRAGMA quick_check` → gzip 圧縮レベル 1。保存は `/home/stock/backups/daily/`（7 世代）と日曜の `/home/stock/backups/weekly/`（4 世代）。R2 には出さない。失敗時は既存世代を消さず、`fail cron` が `#alerts-batch` に page する。復元は [`docs/DESIGN_backup.md`](../docs/DESIGN_backup.md)。

`deploy/crontab` は `provision.sh` が `crontab -u stock` で入れる。デプロイ後に一度だけ手で走らせる（ユーザ `stock`）:

```bash
cd /home/stock/stock-analyzer && deploy/cron-run.sh jobs/ops/backup_db.py
ls -lh /home/stock/backups/daily
tail /var/log/app/backup_db.log
```

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

エージェントの VM / コンテナには mount が無い。同じ bucket を S3 互換 API で読む。

1. エージェントの Secrets / 環境変数に `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` を登録する（R2 側で **Object Read only** のトークンを別に発行する。登録先は上の `VPS_SSH_KEY` と同じ）。
   起動時に環境変数として入る。登録後に始めたエージェントから有効
2. `rclone` は `bash .cursor/install.sh`（環境ビルド。Cursor は自動）で入る。同じスクリプトが `.venv` / `pdftotext` と、Secrets に `JQUANTS_API_KEY` があれば `stock.db`（2 年分）も作る。`VPS_SSH_KEY` があれば続けて本番の `monthly_issuers` / `monthly_tables` / `monthly_elements` / `monthly_values` だけを載せ替える（DB 丸ごとコピーしない。SSH が無い・繋がらないときは skip）
3. 同じスクリプトが `/usr/local/bin/rclone` にラッパーを置く。`R2_*` から remote `r2` を環境変数で定義し（設定ファイルは書かない）、`AWS_CA_BUNDLE` があるプロキシ環境では外して `--ca-cert` で渡し直す（そのままだと S3 が `LoadCustomCABundleError` で落ちる）。export は要らない

```bash
rclone lsd r2:share/monthly/
rclone ls r2:share/monthly/9279/
rclone copy r2:share/monthly/9279/ /tmp/r2/monthly/9279/   # 必要な分だけ /tmp へ
rclone copy r2:share/inventory/vps-packages.txt /tmp/r2/inventory/
rclone copy r2:share/stats/meti/seidou/ /tmp/r2/stats/meti/seidou/
```

- 取得先は `/tmp` 配下。リポジトリに置かない（`*.pdf` は commit 禁止）
- 書き込みはしない。R2 へ書くのは VPS の cron だけ
- 鍵の値を `echo` やログに出さない
