# 銘柄タブ

原文の取込と保存は `docs/DESIGN_xbrl.md` §15。共通見た目は `docs/SCREEN_SPEC.md`。

## 誰のための画面か

1 銘柄の決算短信を、TDnet の PDF と同じ並びで読みたい自分。PDF は TDnet で約 1 か月しか読めないので、それより前の回もここで読む。

## 画面と遷移

1 画面。ヘッダーの `銘柄` タブ（`data-tab="stock"`、ルートは `#stock/{code5}`）。銘柄を選ぶとその銘柄の短信一覧と、既定の 1 件の原文を出す。再読込は選んだ銘柄を復元する（短信は既定の 1 件に戻る）。

## データ形

`GET /api/stock/{code}/filings` の 1 回分。TDnet の XBRL 書類（決算短信・訂正・業績予想の修正など。EDINET の有報は入らない）を提出の新しい順に最大 200 件。各書類に原文の部品（`documents`）が表示順に付く。部品は サマリー（`summary`）、添付資料まるごと（`qualitative`）、`qualitative.htm` の無い書類（REIT・一部の訂正）は本表の iXBRL（`attachment`）をファイル名順。

## 銘柄

**目的。** 銘柄を探し、その銘柄の決算短信を 1 件選んで PDF と同じ中身を読む。

**要素。** 上から:

1. 操作の行 `#st-search`。銘柄の入力 `#st-sym`（銘柄名かコード。候補 `#st-suggest` は `GET /api/stocks/search` の最大 20 件、`4 桁コード 銘柄名`）、短信の選択 `#st-filing`（`YYYY-MM-DD 書類名`。原文のある書類だけ）
2. `#st-title`。銘柄名（太字）、4 桁コード / 市場 / 33 業種
3. `#st-doc`。選んだ書類の部品を上から順に 1 つずつ iframe で出す。iframe は中身の幅・高さまで伸ばし、画面のスクロールで読む。中身は会社が作った HTML のまま（見た目は PDF とほぼ同じ。ページのヘッダー・フッター、ロゴ、FASF マークは無い）

既定で出す書類は、新しい順で最初の決算短信（本決算・中間・四半期）。無ければ一番新しい書類。

**操作。** 入力すると候補を出す。候補をクリックか Enter（先頭の候補）で選ぶ。Esc か外のクリックで候補を閉じる。`#st-filing` を変えるとその書類の原文に差し替える。原文の中のリンクは押しても開かない。

**空とエラー。** 原文のある書類が 1 件も無ければ `表示できる決算短信がありません。` 銘柄がマスタに無ければ `銘柄が見つかりません。` API 失敗は `読み込めませんでした。`

**スマホと PC。** 同じ構造。操作の行は折り返す。原文は幅が約 172mm 固定なので、狭い画面は `#st-doc` が横スクロール。縮めない。iframe は中身の幅・高さまで広げ、中ではスクロールさせない（入れ子のスクロールは指で滑らせると引っかかる）。高さを測り直すのは読み込んだときと画面の幅が変わったときだけ（スマホはスクロールでアドレスバーが出入りして高さだけの resize が続く）。

## API

`GET /api/stock/{code}/filings`（`code` は 4 桁か 5 桁）

```json
{
  "code": "59330", "name": "アルインコ", "market": "プライム", "sector33": "金属製品",
  "filings": [
    {"id": 305, "submitted_at": "2026-10-20 15:30",
     "title": "2027年３月期 第２四半期（中間期）決算短信〔日本基準〕（連結）",
     "doc_type": "quarterly", "period_type": "Q2", "is_amendment": false,
     "documents": [{"seq": 0, "part": "summary", "file_name": "tse-…-ixbrl.htm"},
                   {"seq": 1, "part": "qualitative", "file_name": "qualitative.htm"}]}
  ]
}
```

マスタに無い銘柄は 404、形の違うコードは 400。原文がまだ無い書類は `documents: []`（画面には出さない）。

`GET /api/stock/filings/{filing_id}/documents/{seq}` は原文の HTML（`text/html`）。`script` / `iframe` / `link` / イベント属性 / `javascript:` / 外の画像を除いて返す。ヘッダー `Content-Security-Policy` で script・外への通信を止め、`sandbox allow-same-origin`（親が高さを測るため）。`ETag` は原文の sha256。無ければ 404。

## 対象外

- 単独四半期・通期予想に対する進捗率のパネル、前回の短信との文章の差分、時系列の表、訂正前後の差分（次の段階）
- 決算補足説明資料（別の PDF。ZIP に無い）
- EDINET の有報・半期報の原文
- 原文の印刷・PDF の保存
