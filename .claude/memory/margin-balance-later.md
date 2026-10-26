---
name: margin-balance-later
description: 信用残・空売り残はユーザーが後で DB に入れる予定。今は取込も画面も作らない
metadata:
  node_type: memory
  type: project
  originSessionId: bee1c640-bebf-5cb4-bdff-a9b3ea7bbb70
  modified: 2026-10-26T14:10:24.237Z
---

信用残（と空売り残）は、ユーザーがそのうち DB に入れる予定（2026-10-26 に聞いた）。それまでは取込・テーブル・API・画面を作らない。

**Why:** 置き場所と取込はユーザーが自分で決めるつもりなので、先に作ると二重になる。

**How to apply:** 銘柄の株主タブ（KAB-182）などで信用残が欲しくなっても、仕様の「対象外」に置いたままにする。IR Bank の `get_weekly_margin_balance` で間に合わせることもしない。
