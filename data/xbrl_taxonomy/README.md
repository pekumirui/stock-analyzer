# XBRL taxonomy bundle

Parse はネットワークに出ない。パーサが読む `schemaRef` / `xsd:import` / ラベル linkbase は
すべてこのディレクトリから読む（`FileLoader` の URL→パス対応。ホスト名が先頭ディレクトリ）。

```
data/xbrl_taxonomy/
  disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor.xsd
  www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd
  www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12/tse-ed-t-2014-01-12.xsd
```

既定パスは `XBRL_CACHE_DIR`（未設定ならリポジトリのこのディレクトリ）。
filing の ZIP ツリーは `data/xbrl`。`jobs/ops/migrate_r2_share.py` は `xbrl/_cache` を既定でコピーしない。タクソノミの同梱はこのディレクトリ。

## 中身

本番 R2 `xbrl/_cache` の全ファイル（114 ファイル）をそのままコピーして同梱済み。
2,188 件の filing をネットワークなしで全件パースし errors=0 を確認している。

KAB-73 で EDINET の有報・半期報・四半期報 10 年分が参照する版を足した（jpcrp / jppfs 2016-02-29〜2021-11-01、
jpigp 2019-02-28〜2021-11-01。88 ファイル）。KAB-167 で jpigp 2018-03-31（cor / rt / ラベル
ja+en。4 ファイル）を足した。KAB-324 で 2016-02-29 より前の公表版を足した。金融庁の公表パッケージ
（次世代 2013-08-21、2014-03-10、2014-06-30、2014-09-19、2015-03-10、2015-04-30、2016-03-14）を洗い、
スキーマ URL（`http://disclosure.edinet-fsa.go.jp/taxonomy/...`）からパーサが読むファイルだけ取った
（cor / rt / ラベル ja+en。jppfs は `jppfs_pe_2012.xsd` も）。30 ファイル。

- jpcrp / jppfs: 2013-08-31（次世代。2015年版の適用前の有報・四半期報・半期報）、2015-03-31
- jpsps: 2013-08-31、2014-07-31、2015-03-31（2016年版パッケージでも jpsps は 2015-03-31 のまま）

2014-03-31 の jpcrp / jppfs はホストに無い（2014年3月の更新は jpcrp / jppfs を対象にしていない）。
jpigp は 2017-02-28 以前がホストに無い（同梱の最古は 2018-03-31）。2015-04-30 は臨時報告書
（jpcrp-esr）だけで、有報の jpcrp は 2015-03-31 のまま。表示変換方式（2013年版以前、
`info.edinet-fsa.go.jp`）はインライン XBRL の取込対象外。
2016〜2026 年の見本 73 件
（日本基準 50・IFRS 16・米国基準 7）を `check` で missing=0、ネットワークなしで全件パースできることを確認した。

EDINET の版はスキーマの URL（`http://disclosure.edinet-fsa.go.jp/taxonomy/...`）からそのまま取れる。
足りない版は見本 ZIP を落として `fetch`（このツールだけがネットワークに出る）:

```
python tools/edinet/sample_zips.py --sample-years 10 --out /tmp/edinet-zips   # または --failed
python tools/xbrl/taxonomy_bundle.py fetch --zips /tmp/edinet-zips
python tools/xbrl/taxonomy_bundle.py check --zips /tmp/edinet-zips
```
filing が参照する版は過去年も含めて残す。どの ZIP からも参照されないファイルだけ落としてよい。
配布物はバイト単位で保持する（`.gitattributes` で `-text`。CRLF のファイルがある）。

`check` / `sync` はパーサが実際に読むファイルだけを数える（`collect_parser_taxonomy_urls`）。
汎用ラベル（`*_gla.xml` / `*-gla.xml`）、定義リンク（`*_def.xml`）、
`www.xbrl.org/2003/2008-07-02/*` はパースに使わないので同梱しなくてよい。

## 投入手順

ZIP 全件を走査して参照クロージャを同梱する。`--zips` は `data/xbrl`。`_cache` は移行スクリプトが既定でコピーしないので、キャッシュが要るときはそのディレクトリを別に渡す。

```
python tools/xbrl/taxonomy_bundle.py scan \
  --zips data/xbrl \
  --cache data/xbrl/_cache \
  --out /tmp/taxonomy-urls.txt

python tools/xbrl/taxonomy_bundle.py sync \
  --zips data/xbrl \
  --from-cache data/xbrl/_cache \
  --to data/xbrl_taxonomy

python tools/xbrl/taxonomy_bundle.py check \
  --zips data/xbrl \
  --bundle data/xbrl_taxonomy
```

キャッシュを丸ごとコピーする場合（走査を省略）:

```
python tools/xbrl/taxonomy_bundle.py sync \
  --all-from-cache \
  --from-cache data/xbrl/_cache \
  --to data/xbrl_taxonomy
```

`check` が MISSING を出したら（パーサが読む .xsd / ラベルが欠けている）、その URL をキャッシュへ足して `sync` し直す。
パース失敗の filing は `python jobs/xbrl/xbrl_parse.py --retry-failed`。
