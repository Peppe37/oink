"""Single sign-on with any OpenID Connect provider (Keycloak, Authentik, Authelia, Zitadel,
Google, Microsoft…): authorization code flow with PKCE, ID token verified against the
provider's keys.

Configure with OINK_OIDC_ISSUER, OINK_OIDC_CLIENT_ID, OINK_OIDC_CLIENT_SECRET and register
`<OINK_PUBLIC_URL>/api/auth/oidc/callback` as redirect URI. New people get an account on
first sign-in (OINK_OIDC_AUTO_CREATE); members of OINK_OIDC_ADMIN_GROUP are admins. A
signed-in user can link their existing account from Settings."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from oink.core.config import Settings


class OIDCError(RuntimeError):
    pass


@dataclass
class Pending:
    verifier: str
    nonce: str
    link_user: int | None
    created: float = field(default_factory=time.time)


class OIDC:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.s = settings
        self._client = client
        self._meta: dict[str, Any] | None = None
        self._jwks: jwt.PyJWKClient | None = None
        self.pending: dict[str, Pending] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.s.oidc_issuer and self.s.oidc_client_id)

    @property
    def redirect_uri(self) -> str:
        return f"{self.s.public_url.rstrip('/')}/api/auth/oidc/callback"

    async def _http(self, method: str, url: str, **kw: Any) -> Any:
        client = self._client or httpx.AsyncClient(timeout=20)
        try:
            r = await client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise OIDCError(f"unreachable:{type(exc).__name__}") from exc
        finally:
            if self._client is None:
                await client.aclose()
        if r.status_code >= 400:
            raise OIDCError(f"http_{r.status_code}")
        return r.json()

    async def metadata(self) -> dict[str, Any]:
        if self._meta is None:
            issuer = self.s.oidc_issuer.rstrip("/")
            self._meta = await self._http("GET", f"{issuer}/.well-known/openid-configuration")
        return self._meta

    async def start(self, link_user: int | None = None) -> str:
        """The provider's login URL (state, nonce and PKCE remembered for 10 minutes)."""
        meta = await self.metadata()
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v.created < 600}
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.pending[state] = Pending(verifier, nonce, link_user)
        query = {
            "response_type": "code",
            "client_id": self.s.oidc_client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.s.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{meta['authorization_endpoint']}?{urlencode(query)}"

    async def finish(self, state: str, code: str) -> tuple[dict[str, Any], int | None]:
        """Verified claims of the person who signed in, and the user to link (if any)."""
        pending = self.pending.pop(state, None)
        if pending is None or time.time() - pending.created > 600:
            raise OIDCError("state_invalid")
        meta = await self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.s.oidc_client_id,
            "code_verifier": pending.verifier,
        }
        if self.s.oidc_client_secret:
            data["client_secret"] = self.s.oidc_client_secret
        tokens = await self._http("POST", meta["token_endpoint"], data=data)
        claims = self.verify(str(tokens.get("id_token", "")), pending.nonce, meta)
        if "userinfo_endpoint" in meta and tokens.get("access_token"):
            try:
                info = await self._http(
                    "GET", meta["userinfo_endpoint"],
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )  # fmt: skip
                if info.get("sub") == claims.get("sub"):
                    claims = {**info, **claims}
            except OIDCError:
                pass
        return claims, pending.link_user

    def verify(self, id_token: str, nonce: str, meta: dict[str, Any]) -> dict[str, Any]:
        if not id_token:
            raise OIDCError("no_id_token")
        try:
            if self._jwks is None:
                self._jwks = jwt.PyJWKClient(meta["jwks_uri"])
            key = self._jwks.get_signing_key_from_jwt(id_token)
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                audience=self.s.oidc_client_id,
                issuer=meta.get("issuer"),
            )
        except jwt.PyJWTError as exc:
            raise OIDCError("id_token_invalid") from exc
        if claims.get("nonce") != nonce:
            raise OIDCError("nonce_mismatch")
        return claims

    def is_admin(self, claims: dict[str, Any]) -> bool:
        group = self.s.oidc_admin_group
        if not group:
            return False
        groups = claims.get("groups") or []
        roles = (claims.get("realm_access") or {}).get("roles") or []  # Keycloak
        return group in groups or f"/{group}" in groups or group in roles
