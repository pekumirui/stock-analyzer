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

## 中身

本番 R2 `xbrl/_cache` の全ファイル（114 ファイル・約 107MB）をそのままコピーして同梱済み。
2,188 件の filing をネットワークなしで全件パースし errors=0 を確認している。
filing が参照する版は過去年も含めて残す。どの ZIP からも参照されないファイルだけ落としてよい。
配布物はバイト単位で保持する（`.gitattributes` で `-text`。CRLF のファイルがある）。

`check` は ZIP から辿れる参照をすべて数えるため、パーサが読まない汎用ラベル（`*_gla.xml` /
`*-gla.xml`）、定義リンク（`*_def.xml`）、`www.xbrl.org/2003/2008-07-02/*` の 26 件を
MISSING と出す。これらは本番キャッシュにも無く、パースには不要。

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
