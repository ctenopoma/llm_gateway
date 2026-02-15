"""
Tests for delegated billing — API key + X-User-Oid + X-App-Id scenario.

When an API key carries both X-User-Oid and X-App-Id headers,
billing is delegated to the specified user instead of the key owner.
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.middleware.gateway import _authenticate
from app.models.schemas import ApiKey


def _make_api_key(user_oid: str = "owner-oid-1") -> ApiKey:
    return ApiKey(
        id=uuid4(),
        user_oid=user_oid,
        hashed_key="a" * 64,
        salt="b" * 32,
        display_prefix="sk-gate-abc...",
        scopes=["chat.completions"],
        rate_limit_rpm=60,
        is_active=True,
        expires_at=datetime.now() + timedelta(days=30),
    )


def _make_request(
    bearer_token: str = "sk-gate-test",
    user_oid: str | None = None,
    app_id: str | None = None,
    gateway_secret: str | None = None,
):
    """Build a mock request with the specified headers."""
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if user_oid:
        headers["X-User-Oid"] = user_oid
    if app_id:
        headers["X-App-Id"] = app_id
    if gateway_secret:
        headers["X-Gateway-Secret"] = gateway_secret

    request = MagicMock()
    request.headers = headers
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


@pytest.mark.asyncio
async def test_api_key_only_bills_owner():
    """API key without delegation headers → bill to API key owner."""
    api_key = _make_api_key(user_oid="owner-oid-1")

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            request = _make_request(bearer_token="sk-gate-test")
            user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "owner-oid-1"
    assert key_obj is api_key
    assert app_id is None


@pytest.mark.asyncio
async def test_delegated_billing_both_headers():
    """API key + X-User-Oid + X-App-Id → bill to delegated user."""
    api_key = _make_api_key(user_oid="owner-oid-1")

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                # Return active app
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    user_oid="delegated-user-1",
                    app_id="dify-app-1",
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "delegated-user-1"  # Delegated, not owner
    assert key_obj is api_key  # API key still returned for rate limiting
    assert app_id == "dify-app-1"
    assert key_id == str(api_key.id)


@pytest.mark.asyncio
async def test_delegated_billing_missing_app_id():
    """API key + X-User-Oid only (no X-App-Id) → 401."""
    api_key = _make_api_key()

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            request = _make_request(
                bearer_token="sk-gate-test",
                user_oid="delegated-user-1",
                # no app_id
            )
            with pytest.raises(HTTPException) as exc_info:
                await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "X-App-Id" in exc_info.value.detail


@pytest.mark.asyncio
async def test_delegated_billing_missing_user_oid():
    """API key + X-App-Id only (no X-User-Oid) → 401."""
    api_key = _make_api_key()

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            request = _make_request(
                bearer_token="sk-gate-test",
                app_id="dify-app-1",
                # no user_oid
            )
            with pytest.raises(HTTPException) as exc_info:
                await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "X-User-Oid" in exc_info.value.detail


@pytest.mark.asyncio
async def test_delegated_billing_app_not_found():
    """API key + both headers but app doesn't exist → 401."""
    api_key = _make_api_key()

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock, return_value=None):
                request = _make_request(
                    bearer_token="sk-gate-test",
                    user_oid="delegated-user-1",
                    app_id="nonexistent-app",
                )
                with pytest.raises(HTTPException) as exc_info:
                    await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "Invalid App ID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_delegated_billing_app_disabled():
    """API key + both headers but app is inactive → 403."""
    api_key = _make_api_key()

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": False}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    user_oid="delegated-user-1",
                    app_id="disabled-app",
                )
                with pytest.raises(HTTPException) as exc_info:
                    await _authenticate(request)

    assert exc_info.value.status_code == 403
    assert "App is disabled" in exc_info.value.detail
