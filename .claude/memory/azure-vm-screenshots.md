---
name: azure-vm-screenshots
description: Azure VM で画面のスクリーンショットを撮るとき。shot.py を使う。Playwright は .venv、Chromium はキャッシュのもの
metadata:
  type: reference
---

撮るのは `.cursor/skills/verify-stock-analyzer/helpers/shot.py`（KAB-598）。`.venv` の Python の playwright で、ブラウザは `~/.cache/ms-playwright/chromium-1248/chrome-linux-arm64/chrome`（arm64）を自分で探して使う（パッケージの既定の版のブラウザはこの VM に無い）。node の playwright は入れない。

この VM で最初につまずいた所: Python の playwright に同梱の node が `libatomic.so.1` を要る（`install.sh` が `libatomic1` を入れる。この VM には 2026-11-27 に入れた）。SPA の入口は `/app`（`/` は `/monthly` に 301）。親のチェックアウトの `stock.db` はブランチより古い作りのことがあるので、確認用の uvicorn は別のポート（例 8765）で、`DATABASE_URL` を自分の DB（`jobs/golden_db.py` で作る、または `./run.sh prod`）に向けて立てる。8000 番は止めない。

**Why:** KAB-300 と KAB-597 の見た目確認で、playwright が入っておらず、版の合わないブラウザと古い DB で手間取った。
**How to apply:** 画面の確認（AGENTS.md「見た目の確認はスクリーンショット」）をこの VM でするとき。
