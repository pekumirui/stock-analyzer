# 短信 PDF の回帰チェックの HTML（KAB-244）

`tools/tdnet_pdf/golden_check.py` が読む HTML の全件を置くディレクトリ。設計と使い方は `docs/jobs/xbrl.md` の 16.6。

- `html/{開示番号}.html.gz`: IR BANK の短信 PDF を `tools/tdnet_pdf/convert.py` で変換した HTML（gzip）。
- `html/titles.json`: 開示番号 → 変換で取った題名（`tdnet_pdf_documents.title` と同じもの。訂正・速報値の判定に使う）。
- 中身は git に入れない（`.gitignore`）。この README だけ追跡する。PDF は置かない。
- 作り直すときは `python tools/tdnet_pdf/golden_check.py fetch`。`tests/golden/tdnet_pdf/expected.json` の `html_sha256` と違う HTML になったら `sha256 mismatch` と出す（変換器か PDF が変わった）。
- 正解データ（期待値）は repo の `tests/golden/tdnet_pdf/expected.json`。原因の種類ごとの小さな組は `tests/golden/tdnet_pdf/html/` にあり、pytest が回す。
