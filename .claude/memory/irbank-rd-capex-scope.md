---
name: irbank-rd-capex-scope
description: IR Bank get_financials の研究開発費・設備投資額は有報の「研究開発活動」「設備の状況」と範囲が違う会社がある。自己資本比率は計算値
metadata:
  node_type: memory
  type: reference
  originSessionId: e911a911-f23d-59fa-8fff-14c0be3c1ffe
  modified: 2026-10-23T10:12:57.928Z
---

IR Bank `get_financials` で有報の値を突き合わせるときの注意（KAB-78、2025-06 提出の有報 155 社で確認）。

- `rd_expenses`: 販管費の研究開発費だけを採る会社がある（ダイキン・シャープ・三菱重工などで有報の研究開発活動の節の半分以下）。有報の研究開発活動の節は「221億円」のように丸めて書く会社もある
- `capital_expenditure`: IFRS の会社で CF の有形固定資産の取得に近い値になることがある。有報「設備投資等の概要」とは別物
- `equity_ratio_computed`: 自己資本 ÷ 総資産の計算値。有報の自己資本比率と 2% ほどずれる会社がある
- EPS は有報の記載と全社一致した。一部の銀行（E03530）で桁違いの EPS を返す

**How to apply:** EPS 以外はずれても即バグと決めず、有報 HTML の表示値（ix:nonFraction）で確かめる。関連: [[irbank-segment-revenue-external]]
