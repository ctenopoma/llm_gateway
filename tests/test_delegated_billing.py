"""
Tests for delegated billing — API key + X-User-Oid + X-App-Id scenario.

When an API key carries both X-User-Oid and X-App-Id headers,
billing is delegated to the specified user instead of the key owner.
Also supports URL query params and request body fields.
"""

import json
import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.middleware.gateway import _authenticate, _extract_delegation_from_messages
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
    query_user_oid: str | None = None,
    query_app_id: str | None = None,
    body: dict | None = None,
    method: str = "POST",
):
    """Build a mock request with the specified headers, query params and body."""
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if user_oid:
        headers["X-User-Oid"] = user_oid
    if app_id:
        headers["X-App-Id"] = app_id
    if gateway_secret:
        headers["X-Gateway-Secret"] = gateway_secret

    # Build query_params dict (mimics Starlette QueryParams)
    query_params: dict[str, str] = {}
    if query_user_oid:
        query_params["x_user_oid"] = query_user_oid
    if query_app_id:
        query_params["x_app_id"] = query_app_id

    request = MagicMock()
    request.headers = headers
    request.query_params = query_params
    request.method = method
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    # Mock async request.json()
    if body is not None:
        request.json = AsyncMock(return_value=body)
    else:
        request.json = AsyncMock(side_effect=Exception("No body"))

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
    assert "app" in exc_info.value.detail.lower()


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
    assert "user" in exc_info.value.detail.lower()


# ── Query parameter delegation tests ─────────────────────────────


@pytest.mark.asyncio
async def test_delegated_billing_query_params():
    """API key + query x_user_oid + x_app_id → delegated billing."""
    api_key = _make_api_key(user_oid="owner-oid-1")

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    query_user_oid="dify-user-1",
                    query_app_id="dify-app-1",
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "dify-user-1"
    assert key_obj is api_key
    assert app_id == "dify-app-1"
    assert key_id == str(api_key.id)


@pytest.mark.asyncio
async def test_query_params_take_precedence_over_headers():
    """Query params override X-User-Oid / X-App-Id headers."""
    api_key = _make_api_key(user_oid="owner-oid-1")

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    user_oid="header-user",
                    app_id="header-app",
                    query_user_oid="query-user",
                    query_app_id="query-app",
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "query-user"  # Query takes precedence
    assert app_id == "query-app"     # Query takes precedence


@pytest.mark.asyncio
async def test_query_param_user_only_missing_app():
    """Query x_user_oid only (no x_app_id or header) → 401."""
    api_key = _make_api_key()

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            request = _make_request(
                bearer_token="sk-gate-test",
                query_user_oid="dify-user-1",
                # no app_id anywhere
            )
            with pytest.raises(HTTPException) as exc_info:
                await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "app" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_query_param_app_only_missing_user():
    """Query x_app_id only (no x_user_oid or header) → 401."""
    api_key = _make_api_key()

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            request = _make_request(
                bearer_token="sk-gate-test",
                query_app_id="dify-app-1",
                # no user_oid anywhere
            )
            with pytest.raises(HTTPException) as exc_info:
                await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "user" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_query_param_app_not_found():
    """Query params with non-existent app → 401."""
    api_key = _make_api_key()

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock, return_value=None):
                request = _make_request(
                    bearer_token="sk-gate-test",
                    query_user_oid="dify-user-1",
                    query_app_id="nonexistent-app",
                )
                with pytest.raises(HTTPException) as exc_info:
                    await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "Invalid App ID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_header_fallback_when_no_query_params():
    """Headers are used when query params are absent (backward compat)."""
    api_key = _make_api_key(user_oid="owner-oid-1")

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    user_oid="header-user",
                    app_id="header-app",
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "header-user"
    assert app_id == "header-app"


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


# ── Request body delegation tests ────────────────────────────────


@pytest.mark.asyncio
async def test_delegated_billing_body_params():
    """API key + body x_user_oid + x_app_id → delegated billing."""
    api_key = _make_api_key(user_oid="owner-oid-1")
    body = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "x_user_oid": "body-user-1",
        "x_app_id": "body-app-1",
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    body=body,
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "body-user-1"
    assert key_obj is api_key
    assert app_id == "body-app-1"
    assert key_id == str(api_key.id)


@pytest.mark.asyncio
async def test_body_params_take_precedence_over_headers():
    """Body x_user_oid/x_app_id override X-User-Oid/X-App-Id headers."""
    api_key = _make_api_key(user_oid="owner-oid-1")
    body = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "x_user_oid": "body-user",
        "x_app_id": "body-app",
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    user_oid="header-user",
                    app_id="header-app",
                    body=body,
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "body-user"  # Body takes precedence over headers
    assert app_id == "body-app"


@pytest.mark.asyncio
async def test_query_params_take_precedence_over_body():
    """URL query params override request body fields."""
    api_key = _make_api_key(user_oid="owner-oid-1")
    body = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "x_user_oid": "body-user",
        "x_app_id": "body-app",
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    query_user_oid="query-user",
                    query_app_id="query-app",
                    body=body,
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "query-user"  # Query > Body
    assert app_id == "query-app"


@pytest.mark.asyncio
async def test_body_user_only_missing_app():
    """Body x_user_oid only (no x_app_id) → 401."""
    api_key = _make_api_key()
    body = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "x_user_oid": "body-user-1",
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            request = _make_request(
                bearer_token="sk-gate-test",
                body=body,
            )
            with pytest.raises(HTTPException) as exc_info:
                await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "app" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_body_app_only_missing_user():
    """Body x_app_id only (no x_user_oid) → 401."""
    api_key = _make_api_key()
    body = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "x_app_id": "body-app-1",
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            request = _make_request(
                bearer_token="sk-gate-test",
                body=body,
            )
            with pytest.raises(HTTPException) as exc_info:
                await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "user" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_body_app_not_found():
    """Body params with non-existent app → 401."""
    api_key = _make_api_key()
    body = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "x_user_oid": "body-user-1",
        "x_app_id": "nonexistent-app",
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock, return_value=None):
                request = _make_request(
                    bearer_token="sk-gate-test",
                    body=body,
                )
                with pytest.raises(HTTPException) as exc_info:
                    await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "Invalid App ID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_no_body_falls_back_to_headers():
    """GET request (no body) falls back to headers."""
    api_key = _make_api_key(user_oid="owner-oid-1")

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    user_oid="header-user",
                    app_id="header-app",
                    method="GET",
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "header-user"
    assert app_id == "header-app"


@pytest.mark.asyncio
async def test_body_without_delegation_fields_bills_owner():
    """POST body with model/messages but no x_user_oid/x_app_id → bill owner."""
    api_key = _make_api_key(user_oid="owner-oid-1")
    body = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            request = _make_request(
                bearer_token="sk-gate-test",
                body=body,
            )
            user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "owner-oid-1"
    assert app_id is None


# ── Message content delegation tests (Dify LLM node) ─────────────


class TestExtractDelegationFromMessages:
    """Unit tests for _extract_delegation_from_messages helper."""

    def test_valid_json_in_user_message(self):
        messages = [
            {"role": "user", "content": json.dumps({
                "x_user_oid": "user-1", "x_app_id": "app-1", "message": "hello"
            })}
        ]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "user-1"
        assert aid == "app-1"
        assert messages[0]["content"] == "hello"  # content rewritten

    def test_skips_non_user_roles(self):
        messages = [
            {"role": "system", "content": json.dumps({
                "x_user_oid": "u", "x_app_id": "a", "message": "sys"
            })},
            {"role": "user", "content": "plain text"},
        ]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid is None
        assert aid is None

    def test_plain_text_message_ignored(self):
        messages = [{"role": "user", "content": "just a question"}]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid is None
        assert aid is None

    def test_json_without_delegation_keys_ignored(self):
        messages = [{"role": "user", "content": json.dumps({"foo": "bar"})}]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid is None
        assert aid is None
        assert messages[0]["content"] == json.dumps({"foo": "bar"})  # untouched

    def test_missing_message_field_defaults_to_empty(self):
        messages = [{"role": "user", "content": json.dumps({
            "x_user_oid": "u", "x_app_id": "a"
        })}]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "u"
        assert aid == "a"
        assert messages[0]["content"] == ""  # defaults to empty

    def test_uses_first_matching_message(self):
        messages = [
            {"role": "user", "content": json.dumps({
                "x_user_oid": "first", "x_app_id": "a1", "message": "m1"
            })},
            {"role": "user", "content": json.dumps({
                "x_user_oid": "second", "x_app_id": "a2", "message": "m2"
            })},
        ]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "first"
        assert aid == "a1"
        assert messages[0]["content"] == "m1"
        # second message untouched
        assert "second" in messages[1]["content"]

    def test_whitespace_around_json_is_ok(self):
        messages = [{"role": "user", "content": '  {"x_user_oid":"u","x_app_id":"a","message":"hi"}  '}]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "u"
        assert aid == "a"
        assert messages[0]["content"] == "hi"

    def test_bare_key_value_pairs_without_braces(self):
        """Dify may strip outer {} due to Jinja2 template syntax → still parsed."""
        messages = [{
            "role": "user",
            "content": '"x_user_oid": "test2", "x_app_id": "dify-prod", "message": "hello"'
        }]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "test2"
        assert aid == "dify-prod"
        assert messages[0]["content"] == "hello"

    def test_bare_key_value_pairs_with_whitespace(self):
        """Bare key-value pairs with leading/trailing whitespace."""
        messages = [{
            "role": "user",
            "content": '  "x_user_oid": "u1", "x_app_id": "a1", "message": "hi"  '
        }]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "u1"
        assert aid == "a1"
        assert messages[0]["content"] == "hi"

    def test_bare_key_value_pairs_missing_message(self):
        """Bare key-value pairs without message field → defaults to empty."""
        messages = [{
            "role": "user",
            "content": '"x_user_oid": "u1", "x_app_id": "a1"'
        }]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "u1"
        assert aid == "a1"
        assert messages[0]["content"] == ""

    def test_bare_key_value_pairs_in_multimodal_list(self):
        """Bare key-value pairs inside a multimodal content list text part."""
        messages = [{"role": "user", "content": [
            {"type": "text", "text": '"x_user_oid": "lu", "x_app_id": "la", "message": "list bare"'}
        ]}]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "lu"
        assert aid == "la"
        assert messages[0]["content"][0]["text"] == "list bare"

    def test_multimodal_content_list_with_delegation_json(self):
        """content that is a list with delegation JSON text part → extracted."""
        messages = [{"role": "user", "content": [
            {"type": "text", "text": json.dumps({
                "x_user_oid": "list-user", "x_app_id": "list-app", "message": "hello from list"
            })}
        ]}]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "list-user"
        assert aid == "list-app"
        # Text part should be rewritten
        assert messages[0]["content"][0]["text"] == "hello from list"

    def test_multimodal_content_list_plain_text_ignored(self):
        """content that is a list with plain text (no delegation) → ignored."""
        messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid is None
        assert aid is None

    def test_multimodal_content_list_with_image_and_delegation(self):
        """content list with image + delegation text → delegation extracted from text part."""
        messages = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
            {"type": "text", "text": json.dumps({
                "x_user_oid": "vision-user", "x_app_id": "vision-app", "message": "describe this"
            })}
        ]}]
        uid, aid = _extract_delegation_from_messages(messages)
        assert uid == "vision-user"
        assert aid == "vision-app"
        # Only the matching text part is rewritten
        assert messages[0]["content"][1]["text"] == "describe this"
        # Image part untouched
        assert messages[0]["content"][0]["type"] == "image_url"


@pytest.mark.asyncio
async def test_message_content_delegation_e2e():
    """API key + delegation JSON inside message content → delegated billing + cleaned message."""
    api_key = _make_api_key(user_oid="owner-oid-1")
    body = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": json.dumps({
                "x_user_oid": "dify-user-1",
                "x_app_id": "dify-app-1",
                "message": "こんにちは"
            })},
        ],
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    body=body,
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "dify-user-1"
    assert app_id == "dify-app-1"
    assert key_obj is api_key
    # Message content should be cleaned
    assert body["messages"][1]["content"] == "こんにちは"


@pytest.mark.asyncio
async def test_top_level_body_takes_precedence_over_message_content():
    """Top-level body x_user_oid/x_app_id wins over message content JSON."""
    api_key = _make_api_key(user_oid="owner-oid-1")
    body = {
        "model": "test-model",
        "messages": [
            {"role": "user", "content": json.dumps({
                "x_user_oid": "msg-user",
                "x_app_id": "msg-app",
                "message": "hello"
            })},
        ],
        "x_user_oid": "body-user",
        "x_app_id": "body-app",
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"is_active": True}
                request = _make_request(
                    bearer_token="sk-gate-test",
                    body=body,
                )
                user_oid, key_id, key_obj, app_id = await _authenticate(request)

    assert user_oid == "body-user"  # Top-level wins
    assert app_id == "body-app"


@pytest.mark.asyncio
async def test_message_content_delegation_app_not_found():
    """Delegation via message content with non-existent app → 401."""
    api_key = _make_api_key()
    body = {
        "model": "test-model",
        "messages": [
            {"role": "user", "content": json.dumps({
                "x_user_oid": "u", "x_app_id": "bad-app", "message": "hi"
            })},
        ],
    }

    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock, return_value=api_key):
        with patch("app.middleware.gateway.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_SHARED_SECRET = "secret"
            with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock, return_value=None):
                request = _make_request(
                    bearer_token="sk-gate-test",
                    body=body,
                )
                with pytest.raises(HTTPException) as exc_info:
                    await _authenticate(request)

    assert exc_info.value.status_code == 401
    assert "Invalid App ID" in exc_info.value.detail
