"""OIDC validation, RBAC, and conversation-ownership tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from insurance_ai.api.service import ConversationService
from insurance_ai.config import Settings
from insurance_ai.db.models import Customer
from insurance_ai.security.identity import (
    OIDCAuthenticator,
    Principal,
    admin_principal,
    current_principal,
    customer_principal,
)
from sqlalchemy import select


class _StaticJwksClient:
    def __init__(self, public_key) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token: str):
        return SimpleNamespace(key=self.public_key)


def _authenticator():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        authentication_mode="oidc",
        oidc_issuer="https://identity.assured.test/",
        oidc_audience="assured-api",
        oidc_jwks_url="https://identity.assured.test/jwks.json",
        oidc_leeway_seconds=0,
        _env_file=None,
    )
    authenticator = OIDCAuthenticator(
        settings,
        jwks_client=_StaticJwksClient(private_key.public_key()),
    )
    return authenticator, private_key, settings


def _token(private_key, settings: Settings, **overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": settings.oidc_issuer,
        "aud": settings.oidc_audience,
        "sub": "oidc-user-1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "customer_id": "customer-1",
        "roles": ["customer", "assured-admin"],
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.mark.asyncio
async def test_oidc_authenticator_validates_and_maps_principal_claims():
    authenticator, private_key, settings = _authenticator()
    principal = await authenticator.authenticate(_token(private_key, settings))

    assert principal.subject == "oidc-user-1"
    assert principal.customer_id == "customer-1"
    assert principal.roles == {"customer", "assured-admin"}
    assert principal.authenticated
    assert not principal.is_expired()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-api"},
        {"iss": "https://attacker.invalid/"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
    ],
)
async def test_oidc_authenticator_rejects_wrong_audience_issuer_and_expiry(claims):
    authenticator, private_key, settings = _authenticator()
    with pytest.raises(HTTPException) as exc_info:
        await authenticator.authenticate(_token(private_key, settings, **claims))
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_customer_and_admin_claim_requirements():
    with pytest.raises(HTTPException) as customer_error:
        await customer_principal(Principal("user", None, frozenset()))
    assert customer_error.value.status_code == 403

    with pytest.raises(HTTPException) as admin_error:
        await admin_principal(Principal("user", "customer-1", frozenset({"customer"})))
    assert admin_error.value.status_code == 403

    assert Principal("user", None, frozenset(), expires_at=10).is_expired(now=11)


@pytest.mark.asyncio
async def test_oidc_mode_requires_bearer_credentials(monkeypatch):
    from insurance_ai.security import identity

    monkeypatch.setattr(identity, "get_authenticator", lambda: object())
    with pytest.raises(HTTPException) as exc_info:
        await identity.current_principal(None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.asyncio
async def test_conversation_owner_is_bound_and_cross_subject_access_is_rejected(db):
    customer_id = await db.scalar(select(Customer.id).limit(1))
    owner = Principal("owner-subject", customer_id, frozenset({"customer"}))
    attacker = Principal("attacker-subject", customer_id, frozenset({"customer"}))
    service = ConversationService()

    conversation = await service.get_or_create_conversation(db, None, principal=owner)
    assert conversation.principal_subject == owner.subject
    assert conversation.customer_id == customer_id

    with pytest.raises(HTTPException) as exc_info:
        await service.require_conversation(db, conversation.id, attacker)
    assert exc_info.value.status_code == 403


def test_customer_routes_enforce_owner_and_admin_routes_enforce_role(api_client):
    from insurance_ai.api.app import app

    owner = Principal("owner-subject", None, frozenset({"customer"}))
    attacker = Principal("attacker-subject", None, frozenset({"customer"}))
    admin = Principal("admin-subject", None, frozenset({"assured-admin"}))
    app.dependency_overrides[customer_principal] = lambda: owner
    try:
        created = api_client.post("/api/chat", json={"message": "hello"})
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]

        app.dependency_overrides[customer_principal] = lambda: attacker
        app.dependency_overrides[current_principal] = lambda: attacker
        denied = api_client.post(
            "/api/chat",
            json={"conversation_id": conversation_id, "message": "hello again"},
        )
        assert denied.status_code == 403
        assert api_client.get("/api/admin/conversations").status_code == 403

        app.dependency_overrides[current_principal] = lambda: admin
        assert api_client.get("/api/admin/conversations").status_code == 200
    finally:
        app.dependency_overrides.pop(customer_principal, None)
        app.dependency_overrides.pop(current_principal, None)
