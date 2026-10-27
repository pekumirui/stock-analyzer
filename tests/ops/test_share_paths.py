"""data/ 共有ルート。FUSE の /home/stock/r2-share とは別。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs._r2common import fail_if_mount_down, path_on_r2_share  # noqa: E402
from jobs.share_paths import PREFIXES, share_path, share_root  # noqa: E402

FUSE = "/home/stock/r2-share"


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_default_share_root_is_repo_data() -> None:
    oracle = Path(__file__).resolve().parents[2] / "data"
    got = share_root(environ={})
    if not str(got).endswith("/data"):
        _fail(f"share_root {got}")
    if got == Path(FUSE) or FUSE in got.parts:
        _fail(f"share_root must not be the fuse mount: {got}")
    if got != oracle:
        _fail(f"default root {got} != {oracle}")
    for kind in PREFIXES:
        path = share_path(kind, environ={})
        if path != oracle / kind:
            _fail(f"{kind} {path} != {oracle / kind}")


def test_share_root_env_and_explicit_root() -> None:
    env = share_root(environ={"SHARE_ROOT": "/tmp/share-root"})
    if env != Path("/tmp/share-root"):
        _fail(f"SHARE_ROOT {env}")
    blank = share_root(environ={"SHARE_ROOT": "  "}, repo_root=ROOT)
    if blank != ROOT / "data":
        _fail(f"blank SHARE_ROOT {blank}")
    explicit = share_root(root="/explicit", environ={"SHARE_ROOT": "/from-env"})
    if explicit != Path("/explicit"):
        _fail(f"explicit root {explicit}")


def test_share_path_kinds() -> None:
    repo = Path("/tmp/repo-share")
    for kind in ("monthly", "xbrl", "inventory", "stats", "intraday"):
        got = share_path(kind, environ={}, repo_root=repo)
        if got != repo / "data" / kind:
            _fail(f"{kind} -> {got}")
    overridden = share_path("xbrl", environ={"SHARE_ROOT": "/var/share"})
    if overridden != Path("/var/share/xbrl"):
        _fail(f"env kind path {overridden}")
    try:
        share_path("nope", environ={})
    except ValueError as e:
        if "nope" not in str(e):
            _fail(f"unknown kind message {e}")
    else:
        _fail("unknown kind must raise ValueError")


def test_data_monthly_is_not_on_fuse_root() -> None:
    monthly = share_path("monthly", environ={})
    if path_on_r2_share(str(monthly), FUSE):
        _fail(f"data monthly treated as fuse: {monthly}")
    if not str(monthly).endswith("/data/monthly"):
        _fail(f"monthly path {monthly}")


def test_job_defaults_follow_share_prefixes() -> None:
    from app.config import XBRL_DEST
    from jobs.monthly.ir_monthly_r2 import DEFAULT_DEST as ir_dest
    from jobs.monthly.tdnet_monthly_r2 import DEFAULT_DEST as tdnet_dest
    from jobs.ops.vps_packages_r2 import DEFAULT_DEST as inventory_dest
    from jobs.xbrl.tdnet_xbrl_r2 import DEFAULT_DEST as xbrl_dest

    if tdnet_dest != str(share_path("monthly")) or "data/monthly" not in tdnet_dest:
        _fail(f"tdnet dest {tdnet_dest}")
    if ir_dest != tdnet_dest:
        _fail(f"ir dest {ir_dest}")
    if inventory_dest != str(share_path("inventory")) or "data/inventory" not in inventory_dest:
        _fail(f"inventory dest {inventory_dest}")
    if XBRL_DEST != xbrl_dest or xbrl_dest != str(share_path("xbrl")) or "data/xbrl" not in xbrl_dest:
        _fail(f"xbrl dest {XBRL_DEST!r} {xbrl_dest!r}")
    if path_on_r2_share(tdnet_dest, FUSE) or path_on_r2_share(xbrl_dest, FUSE):
        _fail("job dest must not sit on the fuse root")
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    if "_r2common" in config:
        _fail("app.config must not import jobs._r2common")


def test_fail_if_mount_down_local_dest_without_fuse_line(tmp_path: Path) -> None:
    dest = tmp_path / "data" / "monthly"
    dest.mkdir(parents=True)
    (dest / "sample.txt").write_text("ok", encoding="utf-8")
    rc = fail_if_mount_down(
        "tdnet_monthly_r2",
        dest,
        retries=0,
        proc_mounts=lambda: "tmpfs /tmp tmpfs rw 0 0\n",
        r2_root=FUSE,
    )
    if rc is not None:
        _fail(f"local dest rc {rc}")
