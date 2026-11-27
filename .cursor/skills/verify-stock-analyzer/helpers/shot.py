from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE.parent / ".run" / "state.json"
ARTIFACTS = HERE.parent / "artifacts"
VIEWPORTS = {"pc": {"width": 1280, "height": 900}, "sp": {"width": 390, "height": 844}}
CHROMIUM_GLOBS = (
    "/opt/pw-browsers/chromium-*/chrome-linux*/chrome",
    "~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
    "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
    "~/AppData/Local/ms-playwright/chromium-*/chrome-win*/chrome.exe",
)


def chromium_candidates() -> list[str]:
    found = [os.environ["PLAYWRIGHT_CHROMIUM"]] if os.environ.get("PLAYWRIGHT_CHROMIUM") else []
    for pattern in CHROMIUM_GLOBS:
        found += sorted(glob.glob(os.path.expanduser(pattern)), reverse=True)
    return found


def launch(pw):
    try:
        return pw.chromium.launch()
    except Exception as first:
        for path in chromium_candidates():
            try:
                return pw.chromium.launch(executable_path=path)
            except Exception:
                continue
        raise SystemExit(f"Chromium が起動できない（bash .cursor/install.sh で入る）: {first}")


def base_url(given: str | None) -> str:
    if given:
        return given.rstrip("/")
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))["url"].rstrip("/")
    return "http://127.0.0.1:8000"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SPA の画面を開いてスクリーンショットを撮る")
    p.add_argument("route", help="#stock/68570/financials のようなハッシュ")
    p.add_argument("--name", required=True, help="artifacts/<name>-pc.png の名前")
    p.add_argument("--click", action="append", default=[], help="開いた後に順に押すセレクタ（繰り返し可）")
    p.add_argument("--target", default="body", help="撮る要素のセレクタ")
    p.add_argument("--viewport", choices=("pc", "sp", "both"), default="both")
    p.add_argument("--wait", type=int, default=800, help="開いた後とクリックごとに待つミリ秒")
    p.add_argument("--scale", type=float, default=1, help="deviceScaleFactor（人に見せるなら 2）")
    p.add_argument("--url", help="既定は state.json の url、無ければ http://127.0.0.1:8000")
    args = p.parse_args(argv)

    from playwright.sync_api import sync_playwright

    url = f"{base_url(args.url)}/app{args.route if args.route.startswith('#') else '#' + args.route}"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    out: dict[str, object] = {"url": url, "shots": [], "errors": []}
    names = ("pc", "sp") if args.viewport == "both" else (args.viewport,)
    with sync_playwright() as pw:
        browser = launch(pw)
        for vp in names:
            page = browser.new_page(viewport=VIEWPORTS[vp], device_scale_factor=args.scale)
            page.on("pageerror", lambda e: out["errors"].append(str(e)))
            page.goto(url)
            page.wait_for_timeout(args.wait)
            for sel in args.click:
                page.click(sel)
                page.wait_for_timeout(args.wait)
            path = ARTIFACTS / f"{args.name}-{vp}.png"
            page.locator(args.target).first.screenshot(path=str(path))
            out["shots"].append(str(path))
            page.close()
        browser.close()
    print(json.dumps(out, ensure_ascii=False))
    return 1 if out["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
