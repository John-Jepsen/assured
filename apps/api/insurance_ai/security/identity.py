"""OIDC access-token authentication and application principal dependencies."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from insurance_ai.config import Settings, get_settings

_BEARER = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    customer_id: str | None
    roles: frozenset[str]
    authenticated: bool = True
    expires_at: int | None = None

    def is_expired(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return self.expires_at is not None and self.expires_at <= current


def demo_principal() -> Principal:
    """Development-only identity that preserves the fully local synthetic demo."""
    return Principal(
        subject="synthetic-demo",
        customer_id=None,
        roles=frozenset({"assured-admin"}),
        authenticated=False,
    )


class OIDCAuthenticator:
    """Validate signed access tokens against a cached OIDC JWKS."""

    def __init__(self, settings: Settings, *, jwks_client=None) -> None:
        self.settings = settings
        self.jwks_client = jwks_client or jwt.PyJWKClient(
            settings.oidc_jwks_url or "",
            cache_keys=True,
        )

    async def authenticate(self, token: str) -> Principal:
        try:
            signing_key = await asyncio.to_thread(
                self.jwks_client.get_signing_key_from_jwt,
                token,
            )
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[self.settings.oidc_algorithm],
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer,
                leeway=self.settings.oidc_leeway_seconds,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        roles_value = claims.get(self.settings.oidc_roles_claim, [])
        if isinstance(roles_value, str):
            roles = frozenset(roles_value.split())
        elif isinstance(roles_value, list) and all(
            isinstance(role, str) for role in roles_value
        ):
            roles = frozenset(roles_value)
        else:
            roles = frozenset()
        customer_id = claims.get(self.settings.oidc_customer_id_claim)
        return Principal(
            subject=claims["sub"],
            customer_id=customer_id if isinstance(customer_id, str) else None,
            roles=roles,
            expires_at=int(claims["exp"]),
        )


@lru_cache
def get_authenticator() -> OIDCAuthenticator | None:
    settings = get_settings()
    return OIDCAuthenticator(settings) if settings.authentication_mode == "oidc" else None


async def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> Principal:
    authenticator = get_authenticator()
    if authenticator is None:
        return demo_principal()
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await authenticator.authenticate(credentials.credentials)


async def customer_principal(
    principal: Principal = Depends(current_principal),
) -> Principal:
    if principal.authenticated and not principal.customer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The access token is not associated with a customer",
        )
    return principal


async def admin_principal(
    principal: Principal = Depends(current_principal),
) -> Principal:
    allowed = frozenset(get_settings().oidc_admin_roles)
    if principal.roles.isdisjoint(allowed):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return principal


async def websocket_principal(websocket: WebSocket) -> Principal:
    """Authenticate browser sockets via an HttpOnly cookie or bearer header."""
    authenticator = get_authenticator()
    if authenticator is None:
        return demo_principal()
    settings = get_settings()
    token = websocket.cookies.get(settings.oidc_access_token_cookie)
    authorization = websocket.headers.get("authorization", "")
    if not token and authorization.casefold().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    principal = await authenticator.authenticate(token)
    if not principal.customer_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return principal
