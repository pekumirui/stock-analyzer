"""rclone.conf の [r2] だけ upsert する。他 remote（onedrive など）は残す。

使い方:
  python deploy/write_rclone_conf.py

R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY が無ければ何もしない。
キーは標準出力に出さない。内容が同じならファイルを触らない。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

R2_ENDPOINT = "https://39a6da16651b53d44c459c3a4f4d59f5.r2.cloudflarestorage.com"
SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$", re.M)


def r2_section_body(key: str, secret: str) -> str:
    return (
        "type = s3\n"
        "provider = Cloudflare\n"
        f"access_key_id = {key}\n"
        f"secret_access_key = {secret}\n"
        "region = auto\n"
        f"endpoint = {R2_ENDPOINT}\n"
        "acl = private\n"
        "force_path_style = true\n"
        "v2_auth = false\n"
    )


def upsert_section(text: str, name: str, body: str) -> str:
    """[name] セクションを置き換える。無ければ末尾に足す。他セクションは残す。"""
    block = f"[{name}]\n{body.rstrip()}\n"
    src = text or ""
    matches = list(SECTION_RE.finditer(src))
    for index, match in enumerate(matches):
        if match.group(1) != name:
            continue
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(src)
        prefix = src[:start]
        suffix = src[end:]
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        return prefix + block + suffix
    if src.strip():
        return src.rstrip() + "\n\n" + block
    return block


def apply_r2(existing: str | None, key: str, secret: str) -> str:
    return upsert_section(existing or "", "r2", r2_section_body(key, secret))


def write_conf(path: Path, key: str, secret: str) -> bool:
    """True ならファイルを書いた。キーは返さない。"""
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    new = apply_r2(old, key, secret)
    if old == new:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    part = path.with_name(path.name + ".part")
    part.write_text(new, encoding="utf-8")
    part.chmod(0o600)
    os.replace(part, path)
    path.chmod(0o600)
    return True


def main() -> int:
    key = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
    secret = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
    if not key or not secret:
        return 0
    path = Path.home() / ".config" / "rclone" / "rclone.conf"
    if write_conf(path, key, secret):
        print("updated rclone.conf (keys not printed)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
