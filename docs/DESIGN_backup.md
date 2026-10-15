# 本番 SQLite バックアップ

正本は本ファイル。cron の入れ方は [`deploy/README.md`](../deploy/README.md)。実装時の約束は [`AGENTS.md`](../AGENTS.md)。

## 何をするか

- 対象は本番の `stock.db`（既定 `/home/stock/stock-analyzer/stock.db`）。WAL モードなので `cp` は使わない。
- `jobs/ops/backup_db.py` が Python の sqlite3 backup API で整合コピーし、`PRAGMA quick_check` のあと `gzip` 圧縮レベル 1 で保存する。
- 保存先は VPS ローカルだけ（`/home/stock/backups`）。R2 には出さない。
- 日次は 7 世代、週次（日曜の分）は 4 世代。成功したときだけ古い世代を消す。失敗・容量不足・quick_check 失敗では既存の正常ファイルを触らない。
- 容量が足りないときは取得せず非ゼロ終了する。Slack は他ジョブと同じく `deploy/cron-run.sh` の `fail cron` → fluent-bit → `#alerts-batch`。

## 時刻

毎日 **05:00 JST**。`run_batch`（19:30）と毎時 XBRL（12–23 時）と重ならない隙間。バックアップ中は共有ロックを短く区切る（`pages=100`）。

## 配置

```
/home/stock/backups/daily/stock-YYYY-MM-DD.db.gz
/home/stock/backups/weekly/stock-YYYY-MM-DD.db.gz   # 日曜のみ。日次と hardlink
/home/stock/backups/.tmp/                          # 作業用。実行の前後で空（-wal / -shm 含む）
```

一時コピーはソースの WAL を引き継がない（`journal_mode=DELETE`）。本体を消すときは `-wal` / `-shm` も消す。

## 復元

アプリを止めて WAL を外し、gzip を戻す。動かしたまま上書きしない。

```bash
sudo systemctl stop stock-analyzer stock-analyzer-mcp
cd /home/stock/stock-analyzer
mv -n stock.db stock.db.bak-$(date +%F)
mv -n stock.db-wal stock.db-wal.bak-$(date +%F) 2>/dev/null || true
mv -n stock.db-shm stock.db-shm.bak-$(date +%F) 2>/dev/null || true
gzip -dc /home/stock/backups/daily/stock-YYYY-MM-DD.db.gz > stock.db
# 日曜世代なら weekly/ でも同じ
chown stock:stock stock.db
chmod 644 stock.db
sudo systemctl start stock-analyzer stock-analyzer-mcp
```

戻したファイルで `PRAGMA quick_check` が `ok` であることを確認してからアプリを付ける。ダメなら `stock.db.bak-*` に戻す。