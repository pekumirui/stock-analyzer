---
name: rename-target-fixed-point
description: 本番の月次行名を付け替えるとき、新しい名前は canonicalize + issuer alias を通しても変わらない名前にする
metadata:
  node_type: memory
  type: feedback
  originSessionId: 42cb906a-a6b3-5d3b-8324-b2b12d6c6445
  modified: 2026-10-22T00:36:06.259Z
---

本番の行名を手で付け替える（[[scope-word-fix-manual]]）とき、新しい名前は `canonicalize_element_label(apply_issuer_aliases(code, canonicalize_element_label(name)))` で変わらない名前にする。PDF sidecar（`tdnet_monthly_pdf_sidecar.py`）は DB の保存名を覚えて送るが、ingest API は送られた名前を正規化してから完全一致で行を探すので、正規化で変わる名前は次の取込で別の行になる。

**Why:** KAB-116（2026-10-22）で、78230 の資料どおりの `総売上高` / `延べ来店顧客数` は `総 売上高` / `延べ来店顧 客数` に割られて保存名にできなかった。62380 は既存 alias が `X 売上高` → `X` に寄せていたので、alias も直す必要があった。

**How to apply:** 付け替え前に、全部の新しい名前をこの式に通して確かめる。変わるものは名前を選び直すか alias を直す。sidecar は DB 名から学ぶので、付け替えれば migrate は要らない。
