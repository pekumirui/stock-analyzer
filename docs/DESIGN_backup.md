# 本番 SQLite バックアップ

正本は本ファイル。cron の入れ方は [`deploy/README.md`](../deploy/README.md)。実装時の約束は [`AGENTS.md`](../AGENTS.md)。

## 何をするか

- 対象は本番の `stock.db`（既定 `/home/stock/stock-analyzer/stock.db`）。WAL モードなので `cp` は使わない。
- `jobs/ops/backup_db.py` が Python の sqlite3 backup API で整合コピーし、`PRAGMA quick_check` のあと `gzip` 圧縮レベル 1 で保存する。
- ローカルが成功したあとだけ、その gzip を rclone で OneDrive personal へ送る。既定は `onedrive:stock-analyzer-backups/{daily,weekly}/`。R2 には出さない。
- 世代数は OneDrive 側で持つ（日次 7・週次 4）。VPS は確認済みの直近だけ残す（既定は日次 1・週次 1）。
- VPS の古い世代を消すのは、新しいファイルの中身チェックと OneDrive の送信・確認（と OneDrive 側の世代整理）が成功したあとだけ。送信が失敗した日は VPS の新しいファイルも古い世代も残し、非ゼロ終了する。Slack は他ジョブと同じく `deploy/cron-run.sh` の `fail cron` → fluent-bit → `#alerts-batch`。
- rclone が無い、または remote が無いときは警告してスキップする。ローカルは成功のまま（exit 0）。オフサイト未確認なので VPS の古い世代は消さない。
- 容量が足りないとき・quick_check 失敗では取得せず非ゼロ終了する。既存の正常ファイルを触らない。

## 時刻

毎日 **05:00 JST**。`run_batch`（19:30）と毎時 XBRL（12–23 時）と重ならない隙間。バックアップ中は共有ロックを短く区切る（`pages=100`）。OneDrive 送信は gzip のあと（ファイル約 1GB 級）。タイムアウトは copy 1 時間、check 30 分。

## 配置

```
/home/stock/backups/daily/stock-YYYY-MM-DD.db.gz
/home/stock/backups/weekly/stock-YYYY-MM-DD.db.gz   # 日曜のみ。日次と hardlink
/home/stock/backups/.tmp/                          # 作業用。実行の前後で空（-wal / -shm 含む）

onedrive:stock-analyzer-backups/daily/stock-YYYY-MM-DD.db.gz
onedrive:stock-analyzer-backups/weekly/stock-YYYY-MM-DD.db.gz   # 日曜のみ。別オブジェクト
```

一時コピーはソースの WAL を引き継がない（`journal_mode=DELETE`）。本体を消すときは `-wal` / `-shm` も消す。

OneDrive 側に hardlink は無い。日曜は daily と weekly へ同じ gzip を 2 回送る。消すのは `stock-YYYY-MM-DD.db.gz` だけ。`notes.txt` など命名規則外は残す。

## 環境変数

トークンは環境変数にしない。rclone はユーザ `stock` の `~/.config/rclone/rclone.conf` を読む。CI とリポジトリには置かない。

| 名前 | 既定 | 意味 |
|---|---|---|
| `BACKUP_OFFSITE` | `1` | `0` / `false` / `off` で送信しない（ローカル成功、古い世代は残す） |
| `BACKUP_OFFSITE_REMOTE` | `onedrive` | rclone remote 名（末尾の `:` は不要） |
| `BACKUP_OFFSITE_PATH` | `stock-analyzer-backups` | remote 上のルート |
| `BACKUP_RCLONE_BIN` | `rclone` | rclone 実行ファイル |
| `BACKUP_LOCAL_DAILY_KEEP` | `1` | VPS `daily/` の保持数（1 以上） |
| `BACKUP_LOCAL_WEEKLY_KEEP` | `1` | VPS `weekly/` の保持数 |
| `BACKUP_OFFSITE_DAILY_KEEP` | `7` | OneDrive `daily/` の保持数 |
| `BACKUP_OFFSITE_WEEKLY_KEEP` | `4` | OneDrive `weekly/` の保持数 |

CLI の `--no-offsite` は `BACKUP_OFFSITE=0` と同じ（送信しない・VPS の古い世代は消さない）。

デプロイ（`deploy/update.sh`）が R2 キーを書くときは `[r2]` セクションだけ upsert する。`onedrive` など他 remote は消さない。

## 復元

アプリを止めて WAL を外し、gzip を戻す。動かしたまま上書きしない。直近は VPS ローカルを優先する。古い世代は OneDrive から取る。

```bash
sudo systemctl stop stock-analyzer stock-analyzer-mcp
cd /home/stock/stock-analyzer
mv -n stock.db stock.db.bak-$(date +%F)
mv -n stock.db-wal stock.db-wal.bak-$(date +%F) 2>/dev/null || true
mv -n stock.db-shm stock.db-shm.bak-$(date +%F) 2>/dev/null || true
gzip -dc /home/stock/backups/daily/stock-YYYY-MM-DD.db.gz > stock.db
# 日曜世代なら weekly/ でも同じ。VPS に無い日は:
# rclone copy onedrive:stock-analyzer-backups/daily/stock-YYYY-MM-DD.db.gz /tmp/
# gzip -dc /tmp/stock-YYYY-MM-DD.db.gz > stock.db
chown stock:stock stock.db
chmod 644 stock.db
sudo systemctl start stock-analyzer stock-analyzer-mcp
```

戻したファイルで `PRAGMA quick_check` が `ok` であることを確認してからアプリを付ける。ダメなら `stock.db.bak-*` に戻す。

## 取得ファイルの控え

月次の PDF、XBRL の ZIP、経産省の Excel は、以前は R2 の bucket `share` が VPS の外の控えだった。マウントは `/home/stock/r2-share`。切り替えのあと、R2 はその時点の複製として残す。オブジェクトは消さない。新しいファイルは VPS の `data/` にだけ書く。あとから外へ戻すときは `rclone copy` で `data/` を `r2:share` へ送る。copy だけにする。delete はしない。その cron はまだ入れない。
