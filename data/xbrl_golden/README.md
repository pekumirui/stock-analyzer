# XBRL golden（IRBANK）

`xbrl_financials` とのオフライン突合用。再取得は `match_rate` とは別作業。

## ファイル

- 1 開示期間 = 1 JSON。ファイル名 `{code}_{annual|quarterly}_{YYYYMM}[_Qn].json`
- シード済み: `72030` / `68610` / `99840`（通期 2022/03–2026/03 と Q1 2027/03）
- `INDEX.json` は一覧。`match_rate` は読まない

## スキーマ

```json
{
  "code": "72030",
  "name": "トヨタ自動車",
  "edinet_code": "E02144",
  "period_type": "annual",
  "fiscal_year": "2025/03",
  "quarter": null,
  "accounting_standard": "IFRS",
  "is_consolidated": true,
  "unit": "million_jpy",
  "fetched_at": "2026-09-07T10:49:37+00:00",
  "source": "IRBANK Connect get_financials",
  "metrics": {
    "net_sales": 48036704,
    "operating_income": 4795586,
    "ordinary_income": null,
    "net_income": 4765086,
    "total_assets": 93601350,
    "net_assets": 36878913,
    "total_liabilities": 56722437,
    "operating_cf": 3696934,
    "investing_cf": -4189736
  }
}
```

`fiscal_year` は IRBANK の決算期（YYYY/MM）。四半期も通期期末月（例 Q1 でも `2027/03`）。
`period_type=full_year` の行は突合から除外する。
値の単位は **百万円**。null は欠損（分母に入れない）。

## IRBANK フィールド対応

| IRBANK | golden metrics |
|---|---|
| sales | net_sales |
| operating_profit | operating_income |
| ordinary_income | ordinary_income |
| net_income | net_income |
| total_assets | total_assets |
| equity / net_assets | net_assets |
| liabilities / total_liabilities | total_liabilities |
| operating_cf | operating_cf |
| investing_cf | investing_cf |

再取得は IRBANK Connect を外部で叩く。このリポジトリの CLI はネットワークしない。

```bash
# 既存ファイルの検査
.venv/bin/python tools/xbrl_fetch_golden.py

# 貼り付けた raw JSON から 1 ファイルを書き直す
.venv/bin/python tools/xbrl_fetch_golden.py --from-payload /tmp/irbank.json
```
