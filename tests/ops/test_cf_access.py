"""Cloudflare Access の JWT 検証（app/cf_access.py）。署名・aud・iss・期限のどれが違っても通さない。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-ci-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import cf_access  # noqa: E402
from app.main import app  # noqa: E402

TEAM, AUD = "kabukpi.cloudflareaccess.com", "aud-tag"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Jwks:
    def __init__(self, key) -> None:
        self.key = key.public_key()

    def get_signing_key_from_jwt(self, _token: str):
        return self


def _token(key=KEY, **over) -> str:
    now = int(time.time())
    claims = {"aud": [AUD], "iss": f"https://{TEAM}", "iat": now, "exp": now + 3600,
              "email": "me@example.com", **over}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def _ok(token: str, team: str = TEAM, aud: str = AUD) -> bool:
    return cf_access.verify(token, team, aud, jwks=_Jwks(KEY))


def test_verify_accepts_valid_token() -> None:
    assert _ok(_token())


def test_verify_rejects_bad_tokens() -> None:
    now = int(time.time())
    assert not _ok(_token(key=OTHER_KEY))
    assert not _ok(_token(aud=["other-app"]))
    assert not _ok(_token(iss="https://evil.cloudflareaccess.com"))
    assert not _ok(_token(exp=now - 10, iat=now - 3600))
    assert not _ok("not-a-jwt")
    assert not _ok("")


def test_verify_rejects_without_config() -> None:
    assert not _ok(_token(), team="")
    assert not _ok(_token(), aud="")


def test_endpoint_status(monkeypatch) -> None:
    monkeypatch.setattr(cf_access, "verify", lambda token: token == "good")
    client = TestClient(app)
    ok = client.get("/_auth/cf-access", headers={cf_access.HEADER: "good"})
    assert ok.status_code == 204
    ng = client.get("/_auth/cf-access", headers={cf_access.HEADER: "bad"})
    assert ng.status_code == 401
    assert "www-authenticate" not in ng.headers
    assert client.get("/_auth/cf-access").status_code == 401
