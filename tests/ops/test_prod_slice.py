from __future__ import annotations

import gzip
import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, create_engine

from app.models import Base
from tools.prodslice import load as loader
from tools.prodslice.dump import dump
from tools.prodslice.plan import PLAN, PROD_USER_VERSION, ByCode, ChildOf, UsedBy, parse_codes

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "deploy" / "prod_ro_command.sh"
PROD_RO_SH = ROOT / "deploy" / "prod_ro.sh"
KEYS = ROOT / "deploy" / "ssh" / "prod_ro_authorized_keys"
PYTHON = Path(sys.executable)


def _dummy(col):
    t = col.type
    if isinstance(t, Boolean):
        return 0
    if isinstance(t, (Integer, Numeric, Float)):
        return 0
    if isinstance(t, DateTime):
        return "2026-01-01 00:00:00"
    if isinstance(t, Date):
        return "2026-01-01"
    enums = getattr(t, "enums", None)
    return enums[0] if enums else "x"


def _insert(con: sqlite3.Connection, table: str, **values) -> None:
    for col in Base.metadata.tables[table].columns:
        if col.name in values or col.nullable or col.server_default is not None:
            continue
        if col.primary_key and isinstance(col.type, Integer):
            continue
        arg = getattr(col.default, "arg", None)
        values[col.name] = arg if isinstance(arg, (str, int, float)) else _dummy(col)
    cols = ",".join(values)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(values))})", list(values.values()))


@pytest.fixture
def src_db(tmp_path: Path) -> Path:
    path = tmp_path / "prod.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{path}"))
    con = sqlite3.connect(path)
    for code in ("68570", "72030", "99840"):
        _insert(con, "stocks", code=code, name=f"n{code}", edinet_code=f"E{code[:4]}0")
        _insert(con, "daily_prices", code=code, date="2026-11-27", close=1.0)
    _insert(con, "topix", date="2026-11-27", close=1.0)
    for fid, code in ((1, "68570"), (2, "72030")):
        _insert(con, "xbrl_filings", id=fid, code=code, doc_id=f"D{fid}", source="tdnet")
        _insert(con, "xbrl_contexts", id=fid, filing_id=fid, context_ref=f"c{fid}")
        _insert(con, "xbrl_elements", id=10 * fid, local_name=f"E{fid}", namespace_uri=f"u{fid}")
        _insert(con, "xbrl_facts", id=100 * fid, filing_id=fid, context_id=fid, element_id=10 * fid)
    _insert(con, "xbrl_elements", id=30, local_name="Unused", namespace_uri="u3")
    _insert(con, "xbrl_job_runs", id=1, job_name="j")
    for n in range(3):
        _insert(con, "edinet_filers", edinet_code=f"E{['6857', '7203', '9984'][n]}0")
    _insert(con, "edinet_filers", edinet_code="E99999")
    con.commit()
    con.close()
    return path


def _rows(path: Path, table: str, col: str) -> list:
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute(f"SELECT {col} FROM {table}"))
    finally:
        con.close()


def test_plan_covers_every_model_table_in_dependency_order():
    names = [t for t, _ in PLAN]
    assert len(names) == len(set(names))
    assert set(names) == set(Base.metadata.tables)
    seen: set[str] = set()
    for table, rule in PLAN:
        cols = {c.name for c in Base.metadata.tables[table].columns}
        if isinstance(rule, ByCode):
            assert set(rule.columns) <= cols, table
        if isinstance(rule, ChildOf):
            assert rule.parent in seen and rule.column in cols, table
            assert rule.parent_column in {c.name for c in Base.metadata.tables[rule.parent].columns}
        if isinstance(rule, UsedBy):
            assert rule.column in cols, table
            for t, c in rule.sources:
                assert t in seen and c in {x.name for x in Base.metadata.tables[t].columns}, (table, t, c)
        seen.add(table)


def test_parse_codes_normalizes_and_rejects():
    assert parse_codes("6857, 72030,262A") == ["68570", "72030", "262A0"]
    assert parse_codes("6857,68570") == ["68570"]
    for bad in ("", "6857;rm", "68", "abcd", ",".join(["1301"] * 51 + ["1332"])):
        with pytest.raises(ValueError):
            parse_codes(bad)


def test_dump_keeps_only_the_codes_and_what_they_reference(src_db: Path, tmp_path: Path):
    out = tmp_path / "slice.sqlite"
    counts = dump(src_db, out, ["68570"])
    assert _rows(out, "stocks", "code") == ["68570", "72030", "99840"]
    assert _rows(out, "daily_prices", "code") == ["68570"]
    assert _rows(out, "xbrl_filings", "id") == [1]
    assert _rows(out, "xbrl_contexts", "filing_id") == [1]
    assert _rows(out, "xbrl_facts", "id") == [100]
    assert _rows(out, "xbrl_elements", "id") == [10]
    assert _rows(out, "topix", "date") == ["2026-11-27"]
    assert "E99999" not in _rows(out, "edinet_filers", "edinet_code")
    assert "xbrl_job_runs" not in counts


def test_load_rebuilds_the_local_db_with_branch_schema(src_db: Path, tmp_path: Path):
    out = tmp_path / "slice.sqlite"
    dump(src_db, out, ["72030"])
    db = loader._db_path()
    counts = loader.load(out, db)
    assert counts["xbrl_facts"] == 1
    assert _rows(db, "xbrl_facts", "id") == [200]
    assert _rows(db, "daily_prices", "code") == ["72030"]
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA user_version").fetchone()[0] == PROD_USER_VERSION
    con.close()
    loader.guard(db, force=False)


def test_guard_refuses_a_backfilled_db(tmp_path: Path):
    db = tmp_path / "stock.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{db}"))
    con = sqlite3.connect(db)
    _insert(con, "daily_prices", code="68570", date="2026-11-27", close=1.0)
    con.commit()
    con.close()
    with pytest.raises(SystemExit):
        loader.guard(db, force=False)
    loader.guard(db, force=True)


def test_full_key_fallback_quotes_the_remote_command(monkeypatch):
    monkeypatch.delenv("PROD_RO_SSH", raising=False)
    monkeypatch.setattr(loader, "_configured", lambda host: host == loader.FULL_HOST)
    cmd = loader.ssh_command(["get", "/api/x?a=1&b=2"])
    assert cmd[-3:] == [loader.REMOTE_COMMAND, "get", "'/api/x?a=1&b=2'"]
    monkeypatch.setattr(loader, "_configured", lambda host: host == loader.RO_HOST)
    assert loader.ssh_command(["get", "/api/x?a=1&b=2"])[-3:] == [loader.RO_HOST, "get", "/api/x?a=1&b=2"]


class _Api(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"path": self.path}).encode()
        self.send_response(404 if "missing" in self.path else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _command(original: str, src_db: Path, upstream: str = "http://127.0.0.1:9") -> subprocess.CompletedProcess:
    env = {**os.environ, "SSH_ORIGINAL_COMMAND": original, "PROD_RO_APP_DIR": str(ROOT),
           "PROD_RO_DB": str(src_db), "PROD_RO_PYTHON": str(PYTHON), "PROD_RO_UPSTREAM": upstream}
    return subprocess.run(["bash", str(COMMAND)], capture_output=True, env=env, check=False)


def test_command_slice_returns_gzipped_sqlite(src_db: Path, tmp_path: Path):
    proc = _command("slice 6857", src_db)
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "got.sqlite"
    out.write_bytes(gzip.decompress(proc.stdout))
    assert _rows(out, "xbrl_filings", "id") == [1]


def test_command_get_forwards_one_api_get(src_db: Path, upstream: str):
    proc = _command("get /api/stock/68570/financials?statement=PL&periods=8", src_db, upstream)
    assert proc.returncode == 0, proc.stderr
    head, _, body = proc.stdout.partition(b"\n")
    assert head == b"200 application/json"
    assert json.loads(body) == {"path": "/api/stock/68570/financials?statement=PL&periods=8"}
    assert _command("get /api/missing", src_db, upstream).stdout.startswith(b"404 ")


@pytest.mark.parametrize("original", [
    "", "sh", "slice", "slice 6857;id", "slice 6857 7203", "slice $(id)", "get /etc/passwd",
    "get /api/../app", "get /api/x;id", "get /api/x|id", "get http://example.com/api/x", "rm -rf /",
    "get /api/" + "a" * 2100,
])
def test_command_rejects_anything_else(src_db: Path, original: str):
    proc = _command(original, src_db)
    assert proc.returncode == 2, (original, proc.stdout, proc.stderr)
    assert proc.stdout == b""


def _prod_ro(func: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f"set -euo pipefail; source {PROD_RO_SH.as_posix()!r}; {func}"],
                          capture_output=True, text=True, env={**os.environ, **env}, check=False)


def test_repo_key_file_is_restricted_to_the_command():
    text = KEYS.read_text(encoding="utf-8")
    assert "PRIVATE" not in text
    live = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert live and all(ln.startswith('restrict,command="/home/stock/stock-analyzer/deploy/prod_ro_command.sh" ')
                        for ln in live)
    proc = _prod_ro(f"prod_ro_validate_authorized_keys {KEYS.as_posix()!r}", {})
    assert proc.returncode == 0, proc.stderr
    provision = (ROOT / "deploy" / "provision.sh").read_text(encoding="utf-8")
    assert 'source "$APP_DIR/deploy/prod_ro.sh"' in provision and "if ! prod_ro_apply; then" in provision
    assert os.access(COMMAND, os.X_OK)


def test_apply_appends_once_and_rejects_loose_keys(tmp_path: Path):
    cmd = tmp_path / "prod_ro_command.sh"
    cmd.write_text("#!/bin/sh\n", encoding="utf-8")
    cmd.chmod(cmd.stat().st_mode | stat.S_IXUSR)
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIC1s9nI8rGa78Ro/vU152FHM2kZ89UbCHNYLfgXQLhaE t"
    src = tmp_path / "keys"
    dest = tmp_path / "authorized_keys"
    dest.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA deploy\n")
    env = {"PROD_RO_COMMAND": str(cmd), "PROD_RO_AUTH_SRC": str(src), "PROD_RO_AUTH_DEST": str(dest),
           "PROD_RO_USER": os.environ.get("USER", "nobody")}
    line = f'restrict,command="{cmd}" {pub}'
    src.write_text(line + "\n")
    first = _prod_ro("prod_ro_apply", env)
    assert first.returncode == 0, first.stderr
    second = _prod_ro("prod_ro_apply", env)
    assert "authorized_keys unchanged" in second.stdout
    assert dest.read_text().count(line) == 1
    for bad in (f'command="{cmd}" {pub}', f'restrict,command="/bin/sh" {pub}',
                f'restrict,port-forwarding,command="{cmd}" {pub}', f'restrict,pty,command="{cmd}" {pub}'):
        src.write_text(bad + "\n")
        proc = _prod_ro("prod_ro_apply", env)
        assert proc.returncode != 0, bad


def test_proxy_forwards_api_gets_and_serves_the_rest_locally(tmp_path: Path, monkeypatch):
    from starlette.testclient import TestClient

    from tools.prodslice import proxy

    log = tmp_path / "args"
    fake = tmp_path / "ssh"
    fake.write_text(f'#!/bin/sh\necho "$@" > {log}\nprintf \'200 application/json\\n{{"ok": true}}\'\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("PROD_RO_SSH", "fake-host")
    client = TestClient(proxy.app)
    r = client.get("/api/stock/68570/financials?statement=PL&periods=8")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert log.read_text().split()[-3:] == ["fake-host", "get", "/api/stock/68570/financials?statement=PL&periods=8"]
    assert client.post("/api/v1/tables/68570/ingest").status_code == 405
    assert client.get("/app").status_code == 200
    assert proxy.split_reply(b"oops") == (502, "text/plain; charset=utf-8", b"oops")


def test_run_sh_prod_end_to_end_through_the_command(src_db: Path, tmp_path: Path, monkeypatch, capsys):
    fake = tmp_path / "ssh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "while [[ \"$1\" == -* ]]; do shift 2; done\n"
        "shift\n"
        f"SSH_ORIGINAL_COMMAND=\"$*\" PROD_RO_APP_DIR={str(ROOT)!r} PROD_RO_DB={str(src_db)!r} "
        f"PROD_RO_PYTHON={str(PYTHON)!r} exec bash {str(COMMAND)!r}\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("PROD_RO_SSH", "fake-host")
    assert loader.main(["6857", "9984"]) == 0
    db = loader._db_path()
    assert _rows(db, "daily_prices", "code") == ["68570", "99840"]
    assert _rows(db, "xbrl_facts", "id") == [100]
    assert "68570, 99840" in capsys.readouterr().out
