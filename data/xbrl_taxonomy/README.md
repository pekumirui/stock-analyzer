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
ja+en。4 ファイル）を足した。2016〜2026 年の見本 73 件
（日本基準 50・IFRS 16・米国基準 7）を `check` で missing=0、ネットワークなしで全件パースできることを確認した。

KAB-324 で、インライン XBRL のうち 2016-02-29 より前の版を足した（30 ファイル。cor / rt / ラベル ja+en。
jppfs は import する `jppfs_pe_2012.xsd` も）。金融庁の公表物と公開 URL を突き合わせた結果は次のとおり。

| ファミリー | 2016-02-29 より前に実在する版 | 同梱 |
|---|---|---|
| jpcrp / jppfs | 2013-08-31（次世代）、2015-03-31。2014 年の更新はどちらも変えていない | 両方 |
| jpsps | 2013-08-31、2014-07-31、2015-03-31 | 3 版 |
| jpigp | なし（初版は 2018-03-31。2014 / 2015 / 2016-02-29 は公開ホストに無い） | 追加なし |
| jpdei / common | 2013-08-31 | 同梱済み |

KAB-457 で XBRL International の否定ラベル role（`www.xbrl.org/lrr/role/negated-2009-12-16.xsd`）を足した。
参天製薬（45360）の S100AJZW（2017-06-23 有報）と S1009MMA（2017-02-10 四半期報告書）が、このファイルが同梱に無く
`taxonomy not in bundle` のまま `failed` になっている。この xsd が import するのは同梱済みの
`xbrl-linkbase-2003-12-31.xsd` だけである。本番の failed を読み直すのはデプロイ後に
`deploy/cron-run.sh jobs/edinet/edinet_xbrl.py --retry-failed`（このリポジトリの変更だけでは DB は動かない）。

2014-03-31 の jpcrp / jppfs はホストに無い。2015-04-30 の更新は臨時報告書で、有報の jpcrp は 2015-03-31 のまま。
2016-02-29 の jpsps もホストに無い。表示変換方式（2013年版以前）はインライン XBRL ではないので対象外。

訂正報告書は提出が新しくても元書類の版を参照する。S100M0FI（82540、2016/09/01–2016/11/30 の訂正四半期報告書）は
jpcrp / jppfs の 2015-03-31 を指す。2016 年より前の IFRS は EDINET の jpigp ではなく IFRS 財団タクソノミ
（`xbrl.ifrs.org`）を直接参照する。公開買付（jptoi）・大量保有（jplvh）・臨時報告書（jpsps-esr）は
`edinet_xbrl` の対象外なので同梱していない。

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
