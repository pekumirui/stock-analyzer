---
name: formal-tdnet-pdf-retire
description: "短信 ZIP（source=tdnet）と短信 PDF（source=tdnet_pdf）の filing が同じ開示で両方残らないかを TLA+ で全順序検査する。app/xbrl/jobs.py の parse_manifest_row・_retire_pdf_filing、app/xbrl/pdf/__init__.py の sync_pdf_filing・sync_pdf_filings・_sync・_plan・_zip_parsed、tools/tdnet_pdf/financials.py を変えたとき、または「ZIP と PDF が二重に出る」「PDF が消えたまま」を調べるときに使う。"
---

# formal-tdnet-pdf-retire

1 つの開示について、毎時の `xbrl_parse`（短信 ZIP）と手で流す PDF 一括取込（`tools/tdnet_pdf/financials.py`）が、どの順に割り込んでも次の約束を守るかを TLC で全部の順序について確かめる。

- 約束: 短信 ZIP が `parsed` の開示には PDF 由来の filing を残さない。ZIP が無い・読めない開示には PDF 由来の filing を残す
- 破れたときの害: 同じ開示に XBRL と PDF の 2 本の filing が並び、ZIP で置き換えたはずの PDF の読み取り値が業績詳細などに残る。または、どちらも無くてその期の数字が消える

## 走らせる

```sh
tools/formal/check.sh tdnet_pdf_retire MC.cfg
tools/formal/check.sh tdnet_pdf_retire FindingStaleZipSet.cfg
tools/formal/check.sh tdnet_pdf_retire FindingRetireRaises.cfg
tools/formal/check.sh tdnet_pdf_retire FindingZipRereadFails.cfg
```

- Java 11 以上と `tla2tools.jar` v1.8.0（sha256 `fdffef247f563ef6edbfd44958d2a82db79e7564e07cb673262d896353fa6713`）が要る。jar の場所は `TLA2TOOLS_JAR`、無ければ `~/.cache/stock-analyzer/tla2tools-1.8.0.jar`。無いとき `check.sh` は取得コマンドを出して exit 2
- 1 本 数秒。模型は `tools/formal/tdnet_pdf_retire/TdnetPdfRetire.tla`

今のコードでの正しい結果:

| cfg | 期待 | 意味 |
|---|---|---|
| `MC.cfg` | `No error has been found`（164 distinct states） | 変更の回帰検査。落ちたら変更が約束を壊した |
| `FindingStaleZipSet.cfg` | `NotBothAfterOverlap` 違反 | 既知の不具合 1（下） |
| `FindingRetireRaises.cfg` | `NotBothAfterRetireRaises` 違反 | 既知の不具合 2 |
| `FindingZipRereadFails.cfg` | `NeverNeither` 違反 | 既知の不具合 3 |

Finding の cfg が通るようになったら、その不具合が直ったということ。直した変更の PR で、その cfg の期待を「違反なし」に書き換え、下の既知の不具合から外す。

反例を読むときは pstack の `/statechart` スキルを使う。違反までの最短の道筋を平文で出し、通ったときは状態の移り方の図を描く。

## 模型とコードの対応

| 模型 | コード | 1 つの段とみなす根拠 |
|---|---|---|
| `zip` | `xbrl_filings`（`source="tdnet"`）の `parse_status`。行なしは `"none"` | |
| `pdf` | `xbrl_filings`（`source="tdnet_pdf"`、`doc_id` = 開示番号）の行があるか | |
| `ParseManifestRow` | `app/xbrl/jobs.py` `parse_manifest_row` → `app/xbrl/store.py` `parse_zip_into_filing`（`parsed` / `failed` を commit）。`failed` は `mark_failed` | ZIP の結果は 1 回の commit |
| `RetirePdfFiling` | `parse_manifest_row` の `_retire_pdf_filing` → `sync_pdf_filing`。`_zip_parsed` をその場で読むので古くならない | `_sync` は commit 1 回。SQLite が書き込みを直列にする |
| `RetirePdfFilingRaises` | `_retire_pdf_filing` の `except Exception`（`database is locked` など）。ログだけ出して先へ進む | |
| `SyncPdfFilingsReadsZipSet` | `app/xbrl/pdf/__init__.py` `sync_pdf_filings` の `zip_set` を作る行。取込の最初に 1 回だけ読む | |
| `SyncPdfFilingsSyncs` | `sync_pdf_filings` → `_sync_counted` → `_sync(db, number, number in zip_set, ...)`。`_plan` は `zip_parsed` が真なら `_Absent("zip")` で消し、偽なら作る | `_sync` は commit 1 回。locked のやり直しも同じ `zip_set` を使うので同じ段 |
| `SyncPdfFilingsRaises` | `_sync_failed`（やり直しでも落ちた回） | |
| `overlapped` | `zip_set` を読んでから、その開示を処理するまでの間に ZIP の取込が commit したか | |
| `MaxZipReads = 2` | 初回の取込と、1 回の読み直し（`--reparse`、ZIP の SHA-256 の変化、`--retry-failed`） | |

前提: PDF の HTML・`statements`・`stocks` はそろっている（`_plan` が作る側に倒れる開示）。開示は 1 件。PDF 一括取込は 1 回。`edinet` の filing、正規化（`xbrl_financials`）、`xbrl_pdf_matches` は模型に入れていない。`_plan` のほかの `_Absent` の理由（速報値・REIT など）も入れていない。

## 性質

- `RetiredWhenRunsDoNotOverlap`（MC）: 2 つの処理が重ならず、PDF を消す処理が成功していれば、ZIP と PDF が両方残ることはない
- `BatchLeavesRightStateWhenRunLast`（MC）: PDF 一括取込が ZIP の取込のあとに重ならずに走れば、ちょうどどちらか一方だけが残る
- `NotBothAfterOverlap` / `NotBothAfterRetireRaises` / `NeverNeither`（Finding）: 前提を外した同じ約束

## 既知の不具合（2026-11-24、KAB-554 で発見）

1. **古い `zip_set` で PDF を作り直す**（`FindingStaleZipSet.cfg`）: PDF 一括取込が `zip_set` を読む → 毎時の `xbrl_parse` がその開示の ZIP を `parsed` にして PDF の filing を消す → 一括取込がその開示に来て、古い `zip_set` を信じて PDF の filing を作り直す。ZIP と PDF の両方が残る。実コードでも再現した（`sync_pdf_filings` の途中で ZIP の行を commit し `_retire_pdf_filing` を呼ぶと、最後に `('tdnet','parsed')` と `('tdnet_pdf','parsed')` が両方ある）。一括取込は全件で数時間かかり、毎時の cron と重なる
2. **PDF を消す処理の例外を飲む**（`FindingRetireRaises.cfg`）: ZIP が `parsed` になったあと `_retire_pdf_filing` が `database is locked` などで落ちると、PDF の filing が残る。次に手で一括取込を流すまで両方残る
3. **ZIP の読み直しが失敗すると何も残らない**（`FindingZipRereadFails.cfg`）: ZIP が `parsed` で PDF は消してある → ZIP を読み直して `failed` になる → PDF を戻す経路が `xbrl_parse` に無い。次に手で一括取込を流すまで、その開示の数字が無い

## 本物の不具合か、模型の誤りか

- 反例の各段を上の対応表でコードの行に置き換え、その順に実コードで起こせるかを確かめる。起こせれば本物
- 起こせない段があれば模型の誤り。たいていは、コードでは 1 回の commit なのに模型で 2 段に分けた（またはその逆）か、`_plan` の条件の写し漏れ。模型を直す。性質を弱めて通さない

## コードを変えたら

1. 変更が `description` に挙げた関数に触れるかを見る。触れなければ何もしない
2. 触れるなら、対応表の該当行と模型の動作を変更に合わせて直す。新しい commit の区切り・新しい例外の飲み込み・新しい再読込は、それぞれ別の動作にする
3. 4 本の cfg を流し、上の表の期待と比べる。`MC.cfg` が落ちたら変更が約束を壊したので、反例をコードの言葉で報告する
4. 模型がコードを正しく写していると確かめたら、下の同期コミットを進める

## 同期

- 対象: `app/xbrl/jobs.py`（`parse_manifest_row`・`_retire_pdf_filing`）、`app/xbrl/pdf/__init__.py`（`sync_pdf_filing`・`sync_pdf_filings`・`_sync`・`_plan`・`_zip_parsed`・`_sync_counted`・`_sync_failed`）、`app/xbrl/store.py`（`parse_zip_into_filing`・`mark_failed`・`delete_filing`）、`tools/tdnet_pdf/financials.py`
- 最後に模型とコードの一致を確かめたコミット: `a2867cd`
- `git log a2867cd..HEAD -- app/xbrl/jobs.py app/xbrl/pdf/__init__.py app/xbrl/store.py tools/tdnet_pdf/financials.py` に何か出たら、模型を見直してから同期コミットを進める
