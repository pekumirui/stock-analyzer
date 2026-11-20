# 本番 SQLite バックアップ

本番の `stock.db`（WAL モード）を Web アプリやバッチを止めずにスナップショット取得し、整合性検証と圧縮のうえ OneDrive へ世代保管する仕組みと運用手順をまとめる。あわせて共有ディレクトリ `data/` の退避ジョブ（`backup_data.py`）も扱う。

cron への登録手順は [`deploy/README.md`](../deploy/README.md)、バッチ全体のスケジュールは [バッチ一覧](JOBS.md)、実装規約は [`AGENTS.md`](../AGENTS.md) を参照する。

## 設計方針と全体像

WAL モード稼働中の SQLite ファイルを `cp` で複製すると壊れるため、`jobs/ops/backup_db.py` は SQLite backup API で整合スナップショットを作る。標準ライブラリの `sqlite3` は VFS を差し替えられないため、読み取り元と書き出し先の両方に APSW を使う。

| 項目 | 仕様 |
|---|---|
| バックアップ対象 | `DATABASE_URL` が `sqlite:///` ならそのファイル、未設定なら `<repo>/stock.db`（本番既定は `/home/stock/stock-analyzer/stock.db`） |
| 起動時刻と排他制御 | 毎日 `05:00 JST`（`run_batch` の `19:30` と毎時 XBRL の `12〜23` 時と重ならない枠）。`/home/stock/backups/.backup_db.lock` を非ブロッキング `flock` で取り、先行実行があれば `backup_db skip: already running` を出して終了コード `0` で抜ける |
| 圧縮スナップショット | 書き出し先はカスタム VFS（`jobs/ops/backup_store.py` の `CompressVFS`）で、固定長ブロックを zlib レベル 1 で圧縮する。非圧縮の DB 複製はディスクに置かず、不要になったブロックには `fallocate`（`FALLOC_FL_PUNCH_HOLE`）で穴を空ける |
| オフサイト正本と世代数 | `onedrive:stock-analyzer-backups/daily/` に直近 7 世代、日曜（JST）分は `weekly/` にも直近 4 世代を保管する（R2 には出さない） |
| ローカルファイルの寿命 | OneDrive への転送・照合・世代整理がすべて成功したときだけ、今回作成したローカルの `.db.gz` を削除する。失敗時は今回の `.db.gz` を残して非 0 終了し（`deploy/cron-run.sh` 経由で `#alerts-batch` へ通知）、それ以前からローカルにある世代には触らない |
| `rclone` 未設定時の動作 | `BACKUP_OFFSITE=0`、`--no-offsite`、`rclone` 未導入、または対象リモート未定義のときは警告を出して送信をスキップし、ローカルの `.db.gz` を残したまま終了コード `0` で終える |

## `backup_db.py` のステージと制限時間

ジョブ全体の制限時間は 6 時間（`OVERALL_TIMEOUT_S`）である。各ステージの処理と制限時間は以下のとおりで、上限を超えた場合は `fail backup_db: timeout at <stage>` を出して中断し、書きかけの `.tmp` と `.part` を削除する。

| `stage` | 処理内容 | 制限時間・中断条件 |
|---|---|---|
| `disk` | 前回の `.tmp` 残骸と `.part` を掃除し、`dest` の空き容量が `need`（`src_bytes × 0.75` ベースの圧縮予算 + 安全マージン `MIN_FREE_MARGIN`）以上あることを確認する | 容量不足なら何も書かずに終了コード `1` で終了する |
| `copy` | 元 DB を `SQLITE_OPEN_READONLY` かつ `PRAGMA query_only=ON` で開き、読み取りトランザクション（`BEGIN`〜`COMMIT`）を張ったまま `100` ページずつ圧縮 VFS へコピーする（`BusyError` 時は `0.25` 秒待機）。別プロセスの commit でページ 1 からやり直さずに済み、その間の書き込みは WAL 側へ進む | 2 時間。圧縮予算超過（`compress budget exceeded`）またはディスク満杯（`disk full`）でも中断する |
| `quick_check` | 元 DB の読み取りトランザクションを閉じた後、スナップショット側で `PRAGMA journal_mode=DELETE` と `PRAGMA quick_check` を実行する（本番 DB のジャーナルモードは変えない） | 90 分。結果が `ok` 以外なら破棄して失敗する |
| `gzip` | 圧縮ストアを先頭から読み出して解放（`discard_through`）しながら、`stock-YYYY-MM-DD.db.gz.part`（gzip レベル 1）を書き出す | 60 分 |
| `gzip-test` | 生成した `.part` を最後まで展開読み出しし、先頭 100 バイトの SQLite ヘッダと単一ファイル形式を検査してから `daily/stock-YYYY-MM-DD.db.gz` へリネームする（日曜は `weekly/` へもハードリンクを作る） | 60 分 |
| `offsite` | `rclone copy` で `daily/`（日曜は `weekly/` にも別オブジェクトとして）送り、`rclone check --one-way` で検証する（ハッシュ非対応エラーかつ差分なしのときだけ `rclone lsjson` のバイト数一致で代替）。続いて保持数を超えたリモート世代を削除し、最後に今回のローカル `.db.gz` を消す | 1 呼び出しあたり転送 3 時間・照合 90 分・一覧と削除 2 分・疎通確認 30 秒（全体 6 時間の残り時間が短ければそちらで切る） |

## ファイル配置と環境変数

ローカルと OneDrive 上のパス構成は以下のとおりである。

```text
/home/stock/backups/daily/stock-YYYY-MM-DD.db.gz    # 転送・照合完了まで保持し、成功後に削除
/home/stock/backups/weekly/stock-YYYY-MM-DD.db.gz   # 日曜のみ（daily とハードリンク、成功後に削除）
/home/stock/backups/.tmp/                           # 圧縮ストア（.blk）と作業ファイル。実行前後は空
/home/stock/backups/.backup_db.lock                 # 二重起動防止ロック

onedrive:stock-analyzer-backups/daily/stock-YYYY-MM-DD.db.gz
onedrive:stock-analyzer-backups/weekly/stock-YYYY-MM-DD.db.gz
```

OAuth トークンは環境変数やリポジトリには置かず、ユーザー `stock` の `~/.config/rclone/rclone.conf` に保持する（`deploy/update.sh` が R2 設定を更新する際も `[r2]` セクションのみを書き換え、`onedrive` リモートは残す）。動作を切り替える環境変数は以下のとおりである。

| 変数名 | 既定値 | 用途 |
|---|---|---|
| `BACKUP_OFFSITE` | `1` | `0`・`false`・`off` にすると OneDrive 送信を行わず、ローカルの `.db.gz` を残す（CLI の `--no-offsite` と同じ） |
| `BACKUP_OFFSITE_REMOTE` | `onedrive` | `rclone` のリモート名（末尾の `:` は不要） |
| `BACKUP_OFFSITE_PATH` | `stock-analyzer-backups` | リモート上の保存ルートパス |
| `BACKUP_RCLONE_BIN` | `rclone` | `rclone` 実行ファイルのパスまたはコマンド名 |
| `BACKUP_OFFSITE_DAILY_KEEP` | `7` | OneDrive `daily/` に残す世代数 |
| `BACKUP_OFFSITE_WEEKLY_KEEP` | `4` | OneDrive `weekly/` に残す世代数 |

## 復元手順

稼働中のまま `stock.db` を上書きしてはならない。必ずアプリと MCP を止めて既存の WAL・SHM を外し、`PRAGMA quick_check` が `ok` を返したことを確認してから再起動する。中間 `.gz` をローカルに置かずストリーム展開するため、事前に `df -h /` で展開後の DB が収まる空き容量があることを確認する。

1. サービスを停止し、既存の DB と WAL・SHM ファイルを退避する。
   ```bash
   sudo systemctl stop stock-analyzer stock-analyzer-mcp
   cd /home/stock/stock-analyzer
   mv -n stock.db stock.db.bak-$(date +%F)
   mv -n stock.db-wal stock.db-wal.bak-$(date +%F) 2>/dev/null || true
   mv -n stock.db-shm stock.db-shm.bak-$(date +%F) 2>/dev/null || true
   ```
2. OneDrive（送信失敗でローカルに残っている日なら `/home/stock/backups/daily/`）から展開して権限を整え、整合性を検査する。
   ```bash
   rclone cat onedrive:stock-analyzer-backups/daily/stock-YYYY-MM-DD.db.gz | gzip -dc > stock.db
   # ローカルに残っている世代を使う場合:
   # gzip -dc /home/stock/backups/daily/stock-YYYY-MM-DD.db.gz > stock.db
   chown stock:stock stock.db
   chmod 644 stock.db
   sqlite3 stock.db "PRAGMA quick_check;"
   ```
3. 出力が `ok` であることを確認してからサービスを起動する（`ok` 以外なら展開した `stock.db` を外し、`stock.db.bak-*` を戻す）。
   ```bash
   sudo systemctl start stock-analyzer stock-analyzer-mcp
   ```

## 既にある VPS ローカル世代の整理

`backup_db.py` が削除するのはその実行で生成した `stock-YYYY-MM-DD.db.gz` だけであり、過去の失敗や設定変更前に `/home/stock/backups/` へ残った古いファイルは自動では消さない。ディスクを空ける際は本番削除の承認を取ったうえで、ユーザー `stock` で OneDrive との一致を確認してから削除する。

1. ローカルに残っている世代ファイルを確認する（`notes.txt` など命名規則外のファイルは対象外）。
   ```bash
   find /home/stock/backups/daily /home/stock/backups/weekly \
     -maxdepth 1 -type f -name 'stock-*.db.gz' -printf '%s %p\n' | sort
   ```
2. 各ファイルが OneDrive 側と一致するか `rclone check` で照合する（ハッシュ非対応で失敗する場合は `rclone lsjson` の `Size` と `stat -c %s` の一致を確認し、不一致またはリモート未存在のものは `KEEP` とする）。
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
3. 出力を目視し、`confirmed` になったパスだけを手動で `rm` する（`KEEP` は消さない）。

## 共有ファイルの退避（`backup_data.py`）

以前は R2 バケット `share`（`/home/stock/r2-share`）を控えにしていたが、切り替え後の新規ファイルは VPS の `data/` に書き込み、R2 側は当時の複製として凍結している。現在は `jobs/ops/backup_data.py` が毎日 `03:30 JST` に走り、取り直せない原本ファイルを `onedrive:stock-analyzer-backups/data/` へ送る（`--only xbrl` のように対象を絞って単独実行も可）。

| 対象（`KINDS`） | ローカルパス | 転送方式 | 振る舞い |
|---|---|---|---|
| `xbrl` | `data/xbrl`（TDnet ZIP と `manifest/`） | `rclone copy` + `rclone check --one-way` | TDnet は約 1 か月で ZIP を消すため、VPS と OneDrive の両方に残す（`sync` は使わず削除もしない） |
| `monthly` | `data/monthly` | `rclone copy` + `rclone check --one-way` | 新規・更新分だけを追記コピーし、両方に残す |
| `edinet` | `data/edinet`（EDINET の `*.zip`） | `rclone move`（`--min-age 10m --max-duration 50m --cutoff-mode SOFT`） | 書き込みから 10 分以上経った ZIP を 1 件ずつ転送・確認してから VPS 側と空ディレクトリを消す。読むときは `app/edinet/archive.py` が手元 → EDINET → OneDrive の順に探す（[EDINET の取込](jobs/edinet.md)） |

`data/xbrl_taxonomy`（リポジトリ同梱）や `data/intraday`・`data/stats`・`data/inventory` は `backup_data.py` の対象外である（1 年以上前の決算説明資料の退避は別ジョブ `jobs/market/briefing_offsite.py` が `01:40 JST` に担う）。手元へ戻すときは `rclone copy onedrive:stock-analyzer-backups/data/xbrl data/xbrl` を実行する。

`edinet` の `rclone move` が 50 分の上限に達して終了コード `8` または `10` で止まった場合、1 ファイル以上送れていれば（または残件 0 なら）正常終了（`capped=max-duration`、`left=` に残件数を出力）とし、残りは翌日に回す。ただし 1 件も送れないまま（`moved=0` かつ `left>0`）上限終了した場合や、`rclone` の実エラー・Python 側の 3600 秒タイムアウトに当たった場合は `fail backup_data:` を出して終了コード `1` で失敗させる。
