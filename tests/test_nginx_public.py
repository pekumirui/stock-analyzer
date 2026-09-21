"""nginx 公開 location が provision.sh / 見本に残っていること。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def main() -> None:
    provision = (ROOT / "deploy" / "provision.sh").read_text(encoding="utf-8")
    sample = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    for src, text in (("provision.sh", provision), ("nginx.conf", sample)):
        for needle in (
            "location = /",
            "return 301 /monthly",
            "location ^~ /monthly",
            "location = /api/v1/tables",
            "limit_except GET",
            "location ^~ /app",
            "auth_basic off",
        ):
            if needle not in text:
                _fail(f"{src} missing {needle!r}")
    if "limit_req_zone" not in provision:
        _fail("provision.sh missing limit_req_zone")
    if "Content-Security-Policy" not in provision:
        _fail("provision.sh missing CSP")
    if "stock-analyzer-limit.conf" not in provision:
        _fail("provision.sh must write conf.d limit zone")
    print("ok: nginx public locations")


if __name__ == "__main__":
    main()
