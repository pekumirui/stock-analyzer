# deck_reader

## 問い

決算説明資料（TDnet などの PowerPoint 由来の PDF）を LLM に読ませ、ページの要約と数値ファクト（項目・区分・期間・値・単位）を正確かつ安く取り出せるか。取れれば、エージェントが PDF を毎回読まずに DB の検索で全社を横断できる。

本番（`jobs/` の取込、`presentation_*` テーブル、`docs/DESIGN_*.md`）は別の issue でやる。ここは PoC で、本体の `app/` / `jobs/` / DB には触れない。

## やり方

1. PDF のテキスト層を pypdfium2 で取る。PowerPoint 由来なら数字は正確だが、グラフでは項目との対応が崩れる
2. 表紙・中扉・注意事項のページは LLM に回さない（`extract.classify`）
3. 残りのページは「スライド画像（110dpi の PNG）＋テキスト層」を LLM に渡し、JSON（title / summary / facts）で返させる。数字はテキスト層の表記をそのまま使わせる
4. facts の数字がテキスト層に実在するかを機械的に照合する（`verified`）
5. 出力の形は 2 つ（`run.py --prompt`）。v1 は facts を 1 件ずつ、v2 は表の形（期間の列 × 行）で、行に「どの行の内訳か（`of`）」を書かせる。v2 はスクリプトで v1 と同じ facts に展開し、内訳を足して合計と比べる（検算。合わない行はレポートで赤くする）。値の末尾に重なった単位はどちらでもスクリプトで外す

### 照合の限界

`verified` は「その数字がページのどこかにある」かしか見ない。数字の捏造は見つかるが、実在する数字を別の項目・期間に付けた誤りは見つからない（9B のレーザーテック通期 p9 で実際に起きた）。対応の正しさは、検証レポートで画像と並べて人が確かめる。

## ファイル

| ファイル | 役割 |
|---|---|
| `decks.json` | 試す資料の一覧（id・会社・題名・日付・TDnet の doc_id） |
| `extract.py` | テキスト層・ページ分類・画像・プロンプト・JSON の解析・照合。LLM を呼ばない |
| `fetch.py` | doc_id のある資料を IR Bank から `<root>/pdf/<id>.pdf` に取る |
| `run.py` | 1 ページ 1 回 `claude -p` を起動して読ませ、`<root>/results/<id>_<model>.json` を作る |
| `report.py` | 結果 1 冊を HTML か PDF にする（スライド画像と facts の表を並べ、未照合を赤くする）。画像は読ませたページだけ。id と `--root` で呼ぶ |
| `compare.py` | 2 つの結果をページごとに並べた HTML。`--notes` で画像照合のメモを添える |
| `results/` | 結果の要約（Markdown）。JSON・PDF・HTML はコミットしない |

スクリプトは pypdfium2 と Pillow だけを使う（どちらも本体の `requirements.txt` で入る）。

## 回し方

材料と結果はリポジトリの外（既定 `/workspace/deck-reader/`、`--root` で変える）に置く。PDF はコミットしない。

```bash
R=/workspace/deck-reader; cd analyzer/deck_reader
../../.venv/bin/python fetch.py --root $R                       # doc_id のある資料を取る。無いものは表示どおり手で置く
../../.venv/bin/python run.py toyota_2027q1 --root $R --dry-run  # 起動コマンドだけ確かめる
../../.venv/bin/python run.py toyota_2027q1 --root $R --model haiku --pages 7   # まず 1 ページ
../../.venv/bin/python run.py --all --root $R --model haiku --jobs 4
../../.venv/bin/python run.py toyota_2027q1 --root $R --prompt v2 --effort low   # 安い方式。結果は toyota_2027q1_haiku_v2_elow.json
../../.venv/bin/python report.py toyota_2027q1 --root $R --model haiku   # -> $R/reports/toyota_2027q1_haiku.html
../../.venv/bin/python report.py toyota_2027q1 --root $R --format pdf      # スマホで見る用の PDF（Chromium が要る）
../../.venv/bin/python run.py --pdf /path/to/deck.pdf --id advantest_2027q2 --root $R --pages 5,7   # decks.json に無い PDF
../../.venv/bin/python compare.py --root $R --pages toyota_2027q1:7,11,19 \
    --a "Haiku=$R/results/{deck}_haiku.json" --b "Sonnet=$R/results/{deck}_sonnet.json" -o /tmp/compare.html
```

- 費用は claude.ai の利用枠から出る（`claude -p`）。全社を回すなら利用枠では足りないので、本番では API キーと Batch API にする
- `run.py` の隔離：作業フォルダ `<root>/work/<id>/<model>/p<N>/` に `slide.png` と `text.txt` を置き、読めるのはそこだけ（`Read(//<作業フォルダ>/**)` の許可ルール）。道具は Read だけ。`--setting-sources ""` と `--strict-mcp-config` でリポジトリの指示・メモリ・MCP を読ませない。全許可モードは使わない
- スマホで見るときは `--format pdf` で PDF にして、OneDrive などに置いて開く（HTML はスマホのアプリでうまく開けないことがある）
- 1 ページの `claude -p` の出力（トークン・費用・秒）は作業フォルダの `result.json` に残り、冊の集計は結果 JSON の `stats` に入る

決まった資料を繰り返し試すときは `decks.json` に 1 件足す。その場で 1 冊試すだけなら `run.py --pdf`（`<root>/pdf/<id>.pdf` に写してから読むので、`report.py` / `compare.py` は id で呼べる）。TDnet の資料は IR Bank MCP の `list_disclosures`（`edinet_code` は `edinet_filers`）で doc_id を引く。TDnet に出ていない資料（会社の IR サイトだけのもの）は `doc_id` を null にして `note` に取り方を書く。

## 結果

| 対象 | 日付 | 結論 |
|---|---|---|
| [表の形の出力（v2）と effort で費用を下げる](results/2026-11-03_cost.md) | 2026-11-03 | アドバンテスト 16 ページで $0.065 → $0.041（v2・effort low）。数字は v1 とほぼ同じ（欠け 1 件は不要な年号）、検算 60 件すべて一致。残りの出力は考えた分で、`claude -p` では止められない |
| [Qwen3.5-9B（自前 GPU）と Haiku の比較](results/2026-11-02_poc.md) | 2026-11-02 | 9B は多系列の積み上げ棒で読み飛ばしと捏造、数字の多いページで出力が切れる（3 冊で 7 ページ）。Haiku は 9B が失敗した 8 ページで JSON 正常・未照合 0、地域別 56 件 × 2 ページは画像照合で全件正しい。抽出役は Haiku にする |
