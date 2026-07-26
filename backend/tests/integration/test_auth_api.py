import logging
import shutil
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import Depends

import instaloader_webui.db.migrations as migration_module
from instaloader_webui.api.dependencies import (
    RequestSession,
    require_authenticated_session,
    require_csrf,
)
from instaloader_webui.api.envelope import ApiEnvelope
from instaloader_webui.auth.session_tokens import (
    digest_session_token,
    issue_session_token,
)
from instaloader_webui.config import Settings
from instaloader_webui.db.repositories import AdminRepository, WebSessionRepository

BOOTSTRAP_PASSWORD = "correct-horse-battery-staple"
CHANGED_PASSWORD = "different-long-owner-password"
pytestmark = pytest.mark.anyio


def assert_error_envelope(
    response, *, status_code: int, code: str, message: str
) -> None:
    assert response.status_code == status_code
    assert response.json() == {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message},
        "meta": {},
    }


async def test_login_sets_http_only_cookie_and_requires_password_change(client) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": BOOTSTRAP_PASSWORD},
    )

    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.json()["data"]["must_change_password"] is True
    assert response.json()["data"]["csrf_token"]


async def test_login_persists_only_the_session_digest(
    client, session_factory
) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": BOOTSTRAP_PASSWORD},
    )

    raw_token = response.cookies["iw_session"]
    persisted = WebSessionRepository(session_factory).get_by_token_digest(
        digest_session_token(raw_token)
    )

    assert persisted is not None
    assert persisted.token_digest == digest_session_token(raw_token)
    assert raw_token not in persisted.token_digest


async def test_invalid_credentials_return_safe_error_and_do_not_set_cookie(
    client,
) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "wrong-password-value"},
    )

    assert_error_envelope(
        response,
        status_code=401,
        code="invalid_credentials",
        message="The username or password is incorrect.",
    )
    assert "set-cookie" not in response.headers


async def test_non_ascii_username_returns_safe_invalid_credentials(client) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": "擁有者", "password": "wrong-password-value"},
    )

    assert_error_envelope(
        response,
        status_code=401,
        code="invalid_credentials",
        message="The username or password is incorrect.",
    )


async def test_login_throttle_returns_retry_after_header(client) -> None:
    credentials = {"username": "owner", "password": "wrong-password-value"}
    for _ in range(5):
        response = await client.post("/api/auth/login", json=credentials)
        assert response.status_code == 401

    response = await client.post("/api/auth/login", json=credentials)

    assert_error_envelope(
        response,
        status_code=429,
        code="login_throttled",
        message="Too many login attempts. Try again later.",
    )
    assert int(response.headers["Retry-After"]) > 0


async def test_session_returns_authenticated_administrator(
    authenticated_client,
) -> None:
    response = await authenticated_client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["data"]["username"] == "owner"
    assert response.json()["data"]["must_change_password"] is True
    assert response.json()["data"]["csrf_token"]
    assert "raw_token" not in response.text


async def test_expired_session_is_rejected(client, session_factory) -> None:
    issued = issue_session_token()
    admin = AdminRepository(session_factory).get_single()
    assert admin is not None
    now = datetime.now(UTC)
    WebSessionRepository(session_factory).create(
        admin_user_id=admin.id,
        token_digest=issued.digest,
        now=now - timedelta(days=8),
        expires_at=now - timedelta(seconds=1),
    )
    client.cookies.set("iw_session", issued.raw)

    response = await client.get("/api/auth/session")

    assert_error_envelope(
        response,
        status_code=401,
        code="authentication_required",
        message="Authentication is required.",
    )


async def test_revoked_session_is_rejected(
    authenticated_client, session_factory
) -> None:
    raw_token = authenticated_client.cookies["iw_session"]
    WebSessionRepository(session_factory).revoke(
        token_digest=digest_session_token(raw_token),
        now=datetime.now(UTC),
    )

    response = await authenticated_client.get("/api/auth/session")

    assert_error_envelope(
        response,
        status_code=401,
        code="authentication_required",
        message="Authentication is required.",
    )


async def test_state_change_rejects_missing_csrf(authenticated_client) -> None:
    response = await authenticated_client.post(
        "/api/auth/change-password",
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    assert_error_envelope(
        response,
        status_code=403,
        code="csrf_invalid",
        message="The CSRF token is invalid.",
    )


async def test_state_change_rejects_incorrect_csrf(authenticated_client) -> None:
    response = await authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": "not-the-derived-token"},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


async def test_state_change_rejects_non_ascii_csrf(authenticated_client) -> None:
    response = await authenticated_client.post(
        "/api/auth/change-password",
        headers=[(b"X-CSRF-Token", b"\xff")],
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    assert_error_envelope(
        response,
        status_code=403,
        code="csrf_invalid",
        message="The CSRF token is invalid.",
    )


async def test_password_change_rejects_incorrect_current_password(
    authenticated_client,
) -> None:
    session_response = await authenticated_client.get("/api/auth/session")
    csrf = session_response.json()["data"]["csrf_token"]

    response = await authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "incorrect-current-password",
            "new_password": CHANGED_PASSWORD,
        },
    )

    assert_error_envelope(
        response,
        status_code=401,
        code="invalid_current_password",
        message="The current password is incorrect.",
    )


async def test_password_change_rejects_short_new_password(
    authenticated_client,
) -> None:
    session_response = await authenticated_client.get("/api/auth/session")
    csrf = session_response.json()["data"]["csrf_token"]

    response = await authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": BOOTSTRAP_PASSWORD, "new_password": "too-short"},
    )

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["data"] is None
    assert response.json()["error"]["code"] == "validation_error"
    assert "too-short" not in response.text


async def test_password_change_rejects_reusing_current_password(
    authenticated_client,
) -> None:
    session_response = await authenticated_client.get("/api/auth/session")
    csrf = session_response.json()["data"]["csrf_token"]

    response = await authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": BOOTSTRAP_PASSWORD,
        },
    )

    assert_error_envelope(
        response,
        status_code=422,
        code="password_unchanged",
        message="The new password must be different.",
    )


async def test_password_change_revokes_other_sessions(
    authenticated_client, second_authenticated_client
) -> None:
    session_response = await authenticated_client.get("/api/auth/session")
    csrf = session_response.json()["data"]["csrf_token"]
    changed = await authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    assert changed.status_code == 200
    assert changed.json()["data"]["must_change_password"] is False
    retained = await authenticated_client.get("/api/auth/session")
    revoked = await second_authenticated_client.get("/api/auth/session")
    assert retained.status_code == 200
    assert revoked.status_code == 401


async def test_password_change_allows_login_with_new_password(
    authenticated_client, client
) -> None:
    session_response = await authenticated_client.get("/api/auth/session")
    csrf = session_response.json()["data"]["csrf_token"]
    changed = await authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    response = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": CHANGED_PASSWORD},
    )

    assert changed.status_code == 200
    assert response.status_code == 200
    assert response.json()["data"]["must_change_password"] is False


async def test_password_change_accepts_long_unicode_password(
    authenticated_client, client
) -> None:
    unicode_password = "不同的管理員密碼" * 3
    session_response = await authenticated_client.get("/api/auth/session")
    csrf = session_response.json()["data"]["csrf_token"]
    changed = await authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": unicode_password,
        },
    )

    response = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": unicode_password},
    )

    assert changed.status_code == 200
    assert response.status_code == 200


async def test_forced_password_change_blocks_normal_protected_routes(client) -> None:
    @client.app.get(
        "/api/protected-test",
        response_model=ApiEnvelope[dict[str, bool]],
    )
    def protected_test_route(
        _request_session: Annotated[
            RequestSession, Depends(require_authenticated_session)
        ],
    ) -> ApiEnvelope[dict[str, bool]]:
        return ApiEnvelope(success=True, data={"allowed": True})

    logged_in = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": BOOTSTRAP_PASSWORD},
    )
    blocked = await client.get("/api/protected-test")
    csrf = logged_in.json()["data"]["csrf_token"]
    changed = await client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )
    allowed = await client.get("/api/protected-test")

    assert logged_in.status_code == 200
    assert_error_envelope(
        blocked,
        status_code=403,
        code="password_change_required",
        message="The administrator password must be changed.",
    )
    assert changed.status_code == 200
    assert allowed.status_code == 200
    assert allowed.json()["data"] == {"allowed": True}


async def test_forced_password_change_blocks_normal_csrf_but_allows_bootstrap_routes(
    client,
) -> None:
    @client.app.post(
        "/api/protected-state-change-test",
        response_model=ApiEnvelope[dict[str, bool]],
    )
    def protected_state_change_test_route(
        _request_session: Annotated[RequestSession, Depends(require_csrf)],
    ) -> ApiEnvelope[dict[str, bool]]:
        return ApiEnvelope(success=True, data={"allowed": True})

    logged_in = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": BOOTSTRAP_PASSWORD},
    )
    csrf = logged_in.json()["data"]["csrf_token"]

    blocked = await client.post(
        "/api/protected-state-change-test",
        headers={"X-CSRF-Token": csrf},
    )
    logged_out = await client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": csrf},
    )
    logged_in_again = await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": BOOTSTRAP_PASSWORD},
    )
    changed = await client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": logged_in_again.json()["data"]["csrf_token"]},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    assert_error_envelope(
        blocked,
        status_code=403,
        code="password_change_required",
        message="The administrator password must be changed.",
    )
    assert logged_out.status_code == 200
    assert changed.status_code == 200


async def test_logout_revokes_session_and_clears_cookie(
    authenticated_client,
) -> None:
    session_response = await authenticated_client.get("/api/auth/session")
    csrf = session_response.json()["data"]["csrf_token"]

    response = await authenticated_client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": csrf}
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "iw_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    rejected = await authenticated_client.get("/api/auth/session")
    assert rejected.status_code == 401


async def test_logout_requires_csrf(authenticated_client) -> None:
    response = await authenticated_client.post("/api/auth/logout")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


async def test_secure_cookie_configuration_is_honored(
    secure_test_settings: Settings,
    test_client_factory,
) -> None:
    async with test_client_factory(
        secure_test_settings,
        base_url="https://testserver",
    ) as secure_client:
        response = await secure_client.post(
            "/api/auth/login",
            json={"username": "owner", "password": BOOTSTRAP_PASSWORD},
        )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


async def test_lifespan_migrations_are_independent_of_working_directory(
    test_settings: Settings, tmp_path, monkeypatch, test_client_factory
) -> None:
    unrelated_directory = tmp_path / "unrelated"
    unrelated_directory.mkdir()
    monkeypatch.chdir(unrelated_directory)

    async with test_client_factory(test_settings) as cwd_independent_client:
        response = await cwd_independent_client.get("/api/health")

    assert response.status_code == 200


async def test_lifespan_uses_packaged_migration_assets(
    test_settings: Settings, tmp_path, monkeypatch, test_client_factory
) -> None:
    packaged_root = tmp_path / "installed" / "instaloader_webui"
    packaged_migrations = packaged_root / "migrations"
    source_migrations = migration_module.Path(migration_module.__file__).parents[3] / (
        "migrations"
    )
    shutil.copytree(source_migrations, packaged_migrations)
    monkeypatch.setattr(
        migration_module,
        "files",
        lambda _package: packaged_root,
        raising=False,
    )
    monkeypatch.setattr(
        migration_module,
        "__file__",
        str(tmp_path / "unrelated" / "site-packages" / "module.py"),
    )

    async with test_client_factory(test_settings) as packaged_client:
        response = await packaged_client.get("/api/health")

    assert response.status_code == 200


async def test_authentication_failures_do_not_log_passwords(
    client, caplog
) -> None:
    submitted_password = "password-that-must-never-enter-logs"
    with caplog.at_level(logging.DEBUG):
        response = await client.post(
            "/api/auth/login",
            json={"username": "owner", "password": submitted_password},
        )

    assert response.status_code == 401
    assert submitted_password not in caplog.text
    assert BOOTSTRAP_PASSWORD not in caplog.text
