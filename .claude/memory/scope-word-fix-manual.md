---
name: scope-word-fix-manual
description: "月次の 全店 名残（#502）を外すのは本番で手で付け替える。issuer_aliases + migrate は使わない"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 6d200d59-8641-5dfd-9a86-7889895f6155
  modified: 2026-10-09T11:03:05.047Z
---

9/21 以前の正規化が付けた `X 全店 売上高` など、資料に無い範囲の語を外す修正は、本番の行名を手で付け替える。`config/monthly/issuer_aliases.json` に 旧保存名 → 新しい名前 を足してデプロイの migrate に任せる方式は使わない（KeePer fe9fddc ではそうしていたが、2026-10-09 にアスモで却下された）。

**Why:** ユーザーが「全店を無しにする修正は手動対応」と明言した。

**How to apply:** コード側の PR は木（`config/monthly/monthly_row_order.json`）・docs・テストだけ。行名の付け替えは MCP（add_element → put_values → 旧行を null → delete_element）か人の手作業。誰が・いつやるかは確認してから。関連: [[r2-mount-readable]]
