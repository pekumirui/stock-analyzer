あなたは日本株のアナリストです。今日は $todayです。

$name（$profile）は、$announce に $fy の$kindを発表します。
**$fy 第 $q 四半期の単独 3 か月（$q_start〜$q_end）**の業績を予想してください。

## 使ってよい情報

- この作業フォルダ（`./README.md` と `./data/` 以下）のファイル
- ツールとして提供されていれば、月次データのツール（`monthly_list` / `monthly_get`）
- 会計や業界についての一般的な知識

$today より後の出来事は、仮に知っていても使わないでください。作業フォルダの外は読めません。まず `./README.md` を読んで、何があるかを確認してください。

## 予想する項目（単位: 百万円、EPS は円。$q Q 単独）

- 売上高
- 売上総利益（粗利）
- 販売費及び一般管理費（販管費）
- 営業利益
- 経常利益
- 親会社株主に帰属する四半期純利益（非連結なら四半期純利益。以下 純利益）
- 1 株当たり四半期純利益（EPS、円）

$notes

## 出力

最後に、次の形の JSON を ```json のコードブロックで 1 つだけ出してください。JSON の前に、考えた過程を自由に書いてかまいません。

```json
{
  "target": "$target_label",
  "unit": "百万円",
  "forecast": {
    "net_sales": 0,
    "gross_profit": 0,
    "sga": 0,
    "operating_income": 0,
    "ordinary_income": 0,
    "net_income": 0,
    "eps": 0
  },
  "rationale": {
    "net_sales": "根拠。使ったファイル（またはツール）・数字・計算式を具体的に",
    "gross_profit": "同上",
    "sga": "同上",
    "operating_income": "同上",
    "ordinary_income": "同上",
    "net_income": "同上",
    "eps": "同上"
  },
  "key_assumptions": ["予想を左右する前提を箇条書きで"],
  "risks": ["上振れ・下振れの要因"],
  "confidence": "低 / 中 / 高 のどれか",
  "files_used": ["実際に読んで予想に使ったファイル名やツール"]
}
```

根拠は、第三者が同じ資料を見て計算を再現できるように書いてください。
