# 本番 SQLite バックアップ

正本は本ファイル。cron の入れ方は [`deploy/README.md`](../deploy/README.md)。実装時の約束は [`AGENTS.md`](../AGENTS.md)。

## 何をするか

- 対象は本番の `stock.db`（既定 `/home/stock/stock-analyzer/stock.db`）。WAL モードなので `cp` は使わない。
- `jobs/ops/backup_db.py` が Python の sqlite3 backup API で整合コピーし、`PRAGMA quick_check` のあと `gzip` 圧縮レベル 1 で保存する。
- ローカルが成功したあとだけ、その gzip を rclone で OneDrive personal へ送る。既定は `onedrive:stock-analyzer-backups/{daily,weekly}/`。R2 には出さない。正本は OneDrive。戻すときのダウンロード時間は許容する。
- 世代数は OneDrive 側だけ（日次 7・週次 4）。VPS には置かない。今回書いた gzip を消すのは、送信・確認（`rclone check`、失敗時はサイズ）と OneDrive 側の世代整理が成功したあと。
- どれかが失敗した日は今回の gzip を残し、非ゼロ終了する。それより前から VPS にある世代も消さない。Slack は他ジョブと同じく `deploy/cron-run.sh` の `fail cron` → fluent-bit → `#alerts-batch`。
- rclone が無い、または remote が無いときは警告してスキップする。ローカルは成功のまま（exit 0）。今回のファイルも古い世代も消さない。
- 容量が足りないとき・quick_check 失敗では取得せず非ゼロ終了する。既存のファイルを触らない。作業前に、コピーと gzip が同時に乗る空きがあるかを見る。成功したあとの gzip は消えるので、その分の空きは一時的。

## 時刻

毎日 **05:00 JST**。`run_batch`（19:30）と毎時 XBRL（12–23 時）と重ならない隙間。バックアップ中は共有ロックを短く区切る（`pages=100`）。OneDrive 送信は gzip のあと。タイムアウトは copy 1 時間、check 30 分。

## 配置

```
/home/stock/backups/daily/stock-YYYY-MM-DD.db.gz    # 送信確認が終わるまで。成功したら消す
/home/stock/backups/weekly/stock-YYYY-MM-DD.db.gz   # 日曜のみ。日次と hardlink。成功したら消す
/home/stock/backups/.tmp/                          # 作業用。実行の前後で空（-wal / -shm 含む）

onedrive:stock-analyzer-backups/daily/stock-YYYY-MM-DD.db.gz
onedrive:stock-analyzer-backups/weekly/stock-YYYY-MM-DD.db.gz   # 日曜のみ。別オブジェクト
```

一時コピーはソースの WAL を引き継がない（`journal_mode=DELETE`）。本体を消すときは `-wal` / `-shm` も消す。

OneDrive 側に hardlink は無い。日曜は daily と weekly へ同じ gzip を 2 回送る。VPS から消すのは今回の `stock-YYYY-MM-DD.db.gz` だけ。別の日のローカルファイルと、`notes.txt` など命名規則外は残す。OneDrive は保持数を超えた `stock-*.db.gz` だけ消す。デプロイ前から VPS に残っている世代の消し方は「既にある VPS の世代」。

## 環境変数

トークンは環境変数にしない。rclone はユーザ `stock` の `~/.config/rclone/rclone.conf` を読む。CI とリポジトリには置かない。

| 名前 | 既定 | 意味 |
|---|---|---|
| `BACKUP_OFFSITE` | `1` | `0` / `false` / `off` で送信しない（今回の gzip も古い世代も残す） |
| `BACKUP_OFFSITE_REMOTE` | `onedrive` | rclone remote 名（末尾の `:` は不要） |
| `BACKUP_OFFSITE_PATH` | `stock-analyzer-backups` | remote 上のルート |
| `BACKUP_RCLONE_BIN` | `rclone` | rclone 実行ファイル |
| `BACKUP_OFFSITE_DAILY_KEEP` | `7` | OneDrive `daily/` の保持数 |
| `BACKUP_OFFSITE_WEEKLY_KEEP` | `4` | OneDrive `weekly/` の保持数 |

CLI の `--no-offsite` は `BACKUP_OFFSITE=0` と同じ（送信しない。今回の gzip も古い世代も消さない）。

デプロイ（`deploy/update.sh`）が R2 キーを書くときは `[r2]` セクションだけ upsert する。`onedrive` など他 remote は消さない。

## 復元

アプリを止めて WAL を外し、gzip を戻す。動かしたまま上書きしない。正本は OneDrive。ダウンロードに時間がかかることは許容する。送信に失敗して VPS に残った gzip がある日だけ、そのファイルを使える。

```bash
sudo systemctl stop stock-analyzer stock-analyzer-mcp
cd /home/stock/stock-analyzer
mv -n stock.db stock.db.bak-$(date +%F)
mv -n stock.db-wal stock.db-wal.bak-$(date +%F) 2>/dev/null || true
mv -n stock.db-shm stock.db-shm.bak-$(date +%F) 2>/dev/null || true
rclone copy onedrive:stock-analyzer-backups/daily/stock-YYYY-MM-DD.db.gz /tmp/
gzip -dc /tmp/stock-YYYY-MM-DD.db.gz > stock.db
rm /tmp/stock-YYYY-MM-DD.db.gz
# 日曜世代なら weekly/ でも同じ。送信失敗で VPS に残っている日は:
# gzip -dc /home/stock/backups/daily/stock-YYYY-MM-DD.db.gz > stock.db
chown stock:stock stock.db
chmod 644 stock.db
sudo systemctl start stock-analyzer stock-analyzer-mcp
```

戻したファイルで `PRAGMA quick_check` が `ok` であることを確認してからアプリを付ける。ダメなら `stock.db.bak-*` に戻す。

## 既にある VPS の世代

ジョブは今回書いた `stock-YYYY-MM-DD.db.gz` しか消さない。`/home/stock/backups` に既にあるファイルを空けるのは人が行う。OneDrive に同じものがあると確かめたファイルだけを消し、確かめる前には消さない。cron には入れない。本番の削除なので、実行前に承認を取る。

ユーザ `stock` で。

1. 一覧。`notes.txt` など命名規則外は対象にしない。

```bash
find /home/stock/backups/daily /home/stock/backups/weekly -maxdepth 1 -type f -name 'stock-*.db.gz' -printf '%s %p\n' | sort
```

2. ファイルごとに確認する。`rclone check` が 0 の行だけが OneDrive と一致している。0 以外は残す。

```bash
REMOTE=onedrive:stock-analyzer-backups
for kind in daily weekly; do
  for path in /home/stock/backups/$kind/stock-*.db.gz; do
    [ -f "$path" ] || continue
    name=$(basename "$path")
    if rclone check "$(dirname "$path")" "$REMOTE/$kind" --one-way --include "/$name"; then
      echo "confirmed $kind/$name"
    else
      echo "KEEP $kind/$name"
    fi
  done
done
```

check がハッシュ非対応で失敗するときは、`rclone lsjson` の `Size` と `stat -c %s` が一致するものだけを confirmed にする。サイズが違う、または remote に無いものは KEEP のまま。

3. `confirmed` のパスだけを、出力を見てから消す。`KEEP` は消さない。

```bash
rm /home/stock/backups/daily/stock-YYYY-MM-DD.db.gz
```

このジョブは上の `rm` を走らせない。古い世代が残っている間も、次の実行は新しい gzip の分の空きが要る。足りなければ取得せずに非ゼロ終了し、古いファイルは残る。

## 取得ファイルの控え

月次の PDF、XBRL の ZIP、経産省の Excel は、以前は R2 の bucket `share` が VPS の外の控えだった。マウントは `/home/stock/r2-share`。切り替えのあと、R2 はその時点の複製として残す。オブジェクトは消さない。新しいファイルは VPS の `data/` に書く。

KAB-280 から、`jobs/ops/backup_data.py` が毎日 **03:30 JST** に `data/xbrl`（ZIP と `manifest/`）と `data/monthly` を OneDrive へ送る。TDnet は ZIP を約 1 か月で消すので、VPS のディスクが壊れると取り直せない。

- `rclone copy` で `onedrive:stock-analyzer-backups/data/{xbrl,monthly}/` へ送り、`rclone check --one-way` で確かめる。2 回目からは新しいファイルだけ送る
- `sync` は使わない（VPS で消すと OneDrive からも消える）。VPS 側も OneDrive 側も消さない。世代は持たない（ファイルは足すだけ）
- remote・ルート・`BACKUP_OFFSITE`・`BACKUP_RCLONE_BIN` は上の表と同じ。rclone / remote が無いときは `offsite skipped` でスキップ（exit 0）。送信・確認の失敗は `fail backup_data:` で非ゼロ終了し、`fail cron` が page する
- `--only xbrl` / `--only monthly` で片方だけ
- 03:30 は `data/` に書くジョブ（10〜23 時）と `backup_db`（05:00）の間
- ほかの `data/`（`xbrl_taxonomy` はリポジトリ同梱、`intraday` / `stats` / `inventory`）は対象外
- 戻すときは `rclone copy onedrive:stock-analyzer-backups/data/xbrl data/xbrl`
- `data/edinet`（EDINET の ZIP。KAB-281）だけは `rclone move`（`*.zip` で 10 分より古いもの）。送って確かめたものから VPS 側を消す。1 回は `--max-duration 50m --cutoff-mode SOFT` で区切る。打ち切り（rclone exit 8 / 10）は失敗にせず `left=` を出して exit 0、未送分は翌日。途中までの送信成功分だけ VPS から消える。rclone の本当のエラーと、3600 秒の Python timeout（rclone が止まらないハング）は今までどおり `fail backup_data:`。読むときは `app/edinet/archive.py` が手元 → EDINET → OneDrive の順に探す（`docs/DESIGN_edinet.md` §10）
