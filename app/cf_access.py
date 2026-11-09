from functools import lru_cache

import jwt
from fastapi import APIRouter, Request, Response

from .config import CF_ACCESS_AUD, CF_ACCESS_TEAM_DOMAIN

HEADER = "Cf-Access-Jwt-Assertion"

router = APIRouter()


@lru_cache(maxsize=4)
def _jwks(team_domain: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs", lifespan=3600, timeout=5)


def verify(token: str, team_domain: str = CF_ACCESS_TEAM_DOMAIN, aud: str = CF_ACCESS_AUD,
           jwks: jwt.PyJWKClient | None = None) -> bool:
    if not token or not team_domain or not aud:
        return False
    try:
        key = (jwks or _jwks(team_domain)).get_signing_key_from_jwt(token).key
        jwt.decode(
            token, key, algorithms=["RS256"], audience=aud, issuer=f"https://{team_domain}",
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except (jwt.PyJWTError, OSError):
        return False
    return True


# nginx の auth_request から呼ぶ。401 に WWW-Authenticate を付けると Basic 認証のダイアログを上書きする
@router.get("/_auth/cf-access", include_in_schema=False)
def cf_access(request: Request) -> Response:
    ok = verify(request.headers.get(HEADER, ""))
    return Response(status_code=204 if ok else 401, headers={"Cache-Control": "no-store"})
