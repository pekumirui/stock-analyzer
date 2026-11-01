---
name: azure-vm-screenshots
description: Azure VM で画面のスクリーンショットを撮るとき。playwright は入っておらず、chromium だけキャッシュにある
metadata:
  type: reference
---

Azure VM（azureuser）には node の playwright も npm も、`.venv` の playwright も無い。chromium は `~/.cache/ms-playwright/chromium-1248/chrome-linux-arm64/chrome`（arm64）にある。

撮るときは使い捨ての venv（例 `/tmp/pwv`）に `pip install playwright` を入れ、`chromium.launch(executable_path=上のパス)` で起動する。プロジェクトの `.venv` や requirements には入れない。8000 番は止めないので、確認用の uvicorn は別のポート（例 8765）で立てる。

**Why:** KAB-300 のチャートの見た目確認で、`require('playwright')` が通らず手間取った。
**How to apply:** 画面の確認（AGENTS.md「見た目の確認はスクリーンショット」）をこの VM でするとき。
