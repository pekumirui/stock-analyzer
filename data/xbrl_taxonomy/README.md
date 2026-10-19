# XBRL taxonomy bundle

Parse はネットワークに出ない。`schemaRef` / `xsd:import` / `linkbaseRef` が指すファイルは
すべてこのディレクトリから読む（`FileLoader` の URL→パス対応。ホスト名が先頭ディレクトリ）。

```
data/xbrl_taxonomy/
  disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor.xsd
  www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd
  www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12/tse-ed-t-2014-01-12.xsd
```

既定パスは `XBRL_CACHE_DIR`（未設定ならリポジトリのこのディレクトリ）。
本番の R2 `xbrl/_cache` は移行後もしばらく残してよい。

## この PR 時点では中身は空

クラウドの作業 VM から R2 マウント（`/mnt/r2/xbrl/_cache` や
`/home/stock/r2-share/xbrl/_cache`）に届かない。部分セットをコミットしない。

既存キャッシュ全体は約 **107MB**（disclosure.edinet-fsa.go.jp / www.xbrl.org /
www.xbrl.tdnet.info）。filing が参照する版は過去年も含めて残す。どの ZIP からも
参照されないファイルだけ落としてよい。

## 投入手順（R2 が見える箱で）

ZIP 全件を走査して参照クロージャを同梱する:

```
python tools/xbrl/taxonomy_bundle.py scan \
  --zips /home/stock/r2-share/xbrl \
  --cache /home/stock/r2-share/xbrl/_cache \
  --out /tmp/taxonomy-urls.txt

python tools/xbrl/taxonomy_bundle.py sync \
  --zips /home/stock/r2-share/xbrl \
  --from-cache /home/stock/r2-share/xbrl/_cache \
  --to data/xbrl_taxonomy

python tools/xbrl/taxonomy_bundle.py check \
  --zips /home/stock/r2-share/xbrl \
  --bundle data/xbrl_taxonomy
```

キャッシュを丸ごとコピーする場合（走査を省略。107MB 前後）:

```
python tools/xbrl/taxonomy_bundle.py sync \
  --all-from-cache \
  --from-cache /home/stock/r2-share/xbrl/_cache \
  --to data/xbrl_taxonomy
```

`check` が MISSING を出したら、その URL をキャッシュへ足して `sync` し直す。
パース失敗の filing は `python jobs/xbrl/xbrl_parse.py --retry-failed`。
