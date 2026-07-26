import logging
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from instaloader_webui.auth.session_tokens import (
    digest_session_token,
    issue_session_token,
)
from instaloader_webui.config import Settings
from instaloader_webui.db.repositories import AdminRepository, WebSessionRepository
from instaloader_webui.main import create_app

BOOTSTRAP_PASSWORD = "correct-horse-battery-staple"
CHANGED_PASSWORD = "different-long-owner-password"


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


def test_login_sets_http_only_cookie_and_requires_password_change(client) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": BOOTSTRAP_PASSWORD},
    )

    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.json()["data"]["must_change_password"] is True
    assert response.json()["data"]["csrf_token"]


def test_login_persists_only_the_session_digest(client, session_factory) -> None:
    response = client.post(
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


def test_invalid_credentials_return_safe_error_and_do_not_set_cookie(client) -> None:
    response = client.post(
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


def test_non_ascii_username_returns_safe_invalid_credentials(client) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "擁有者", "password": "wrong-password-value"},
    )

    assert_error_envelope(
        response,
        status_code=401,
        code="invalid_credentials",
        message="The username or password is incorrect.",
    )


def test_login_throttle_returns_retry_after_header(client) -> None:
    credentials = {"username": "owner", "password": "wrong-password-value"}
    for _ in range(5):
        assert client.post("/api/auth/login", json=credentials).status_code == 401

    response = client.post("/api/auth/login", json=credentials)

    assert_error_envelope(
        response,
        status_code=429,
        code="login_throttled",
        message="Too many login attempts. Try again later.",
    )
    assert int(response.headers["Retry-After"]) > 0


def test_session_returns_authenticated_administrator(authenticated_client) -> None:
    response = authenticated_client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["data"]["username"] == "owner"
    assert response.json()["data"]["must_change_password"] is True
    assert response.json()["data"]["csrf_token"]
    assert "raw_token" not in response.text


def test_expired_session_is_rejected(client, session_factory) -> None:
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

    response = client.get("/api/auth/session")

    assert_error_envelope(
        response,
        status_code=401,
        code="authentication_required",
        message="Authentication is required.",
    )


def test_revoked_session_is_rejected(
    authenticated_client, session_factory
) -> None:
    raw_token = authenticated_client.cookies["iw_session"]
    WebSessionRepository(session_factory).revoke(
        token_digest=digest_session_token(raw_token),
        now=datetime.now(UTC),
    )

    response = authenticated_client.get("/api/auth/session")

    assert_error_envelope(
        response,
        status_code=401,
        code="authentication_required",
        message="Authentication is required.",
    )


def test_state_change_rejects_missing_csrf(authenticated_client) -> None:
    response = authenticated_client.post(
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


def test_state_change_rejects_incorrect_csrf(authenticated_client) -> None:
    response = authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": "not-the-derived-token"},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_password_change_rejects_incorrect_current_password(
    authenticated_client,
) -> None:
    csrf = authenticated_client.get("/api/auth/session").json()["data"]["csrf_token"]

    response = authenticated_client.post(
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


def test_password_change_rejects_short_new_password(authenticated_client) -> None:
    csrf = authenticated_client.get("/api/auth/session").json()["data"]["csrf_token"]

    response = authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": BOOTSTRAP_PASSWORD, "new_password": "too-short"},
    )

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["data"] is None
    assert response.json()["error"]["code"] == "validation_error"
    assert "too-short" not in response.text


def test_password_change_rejects_reusing_current_password(
    authenticated_client,
) -> None:
    csrf = authenticated_client.get("/api/auth/session").json()["data"]["csrf_token"]

    response = authenticated_client.post(
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


def test_password_change_revokes_other_sessions(
    authenticated_client, second_authenticated_client
) -> None:
    csrf = authenticated_client.get("/api/auth/session").json()["data"]["csrf_token"]
    changed = authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    assert changed.status_code == 200
    assert changed.json()["data"]["must_change_password"] is False
    assert authenticated_client.get("/api/auth/session").status_code == 200
    assert second_authenticated_client.get("/api/auth/session").status_code == 401


def test_password_change_allows_login_with_new_password(
    authenticated_client, client
) -> None:
    csrf = authenticated_client.get("/api/auth/session").json()["data"]["csrf_token"]
    changed = authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": CHANGED_PASSWORD,
        },
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": CHANGED_PASSWORD},
    )

    assert changed.status_code == 200
    assert response.status_code == 200
    assert response.json()["data"]["must_change_password"] is False


def test_password_change_accepts_long_unicode_password(
    authenticated_client, client
) -> None:
    unicode_password = "不同的管理員密碼" * 3
    csrf = authenticated_client.get("/api/auth/session").json()["data"]["csrf_token"]
    changed = authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": unicode_password,
        },
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": unicode_password},
    )

    assert changed.status_code == 200
    assert response.status_code == 200


def test_logout_revokes_session_and_clears_cookie(authenticated_client) -> None:
    csrf = authenticated_client.get("/api/auth/session").json()["data"]["csrf_token"]

    response = authenticated_client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": csrf}
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "iw_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert authenticated_client.get("/api/auth/session").status_code == 401


def test_logout_requires_csrf(authenticated_client) -> None:
    response = authenticated_client.post("/api/auth/logout")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_secure_cookie_configuration_is_honored(
    secure_test_settings: Settings,
) -> None:
    with TestClient(
        create_app(secure_test_settings), base_url="https://testserver"
    ) as secure_client:
        response = secure_client.post(
            "/api/auth/login",
            json={"username": "owner", "password": BOOTSTRAP_PASSWORD},
        )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_lifespan_migrations_are_independent_of_working_directory(
    test_settings: Settings, tmp_path, monkeypatch
) -> None:
    unrelated_directory = tmp_path / "unrelated"
    unrelated_directory.mkdir()
    monkeypatch.chdir(unrelated_directory)

    with TestClient(create_app(test_settings)) as cwd_independent_client:
        response = cwd_independent_client.get("/api/health")

    assert response.status_code == 200


def test_authentication_failures_do_not_log_passwords(
    client, caplog
) -> None:
    submitted_password = "password-that-must-never-enter-logs"
    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/api/auth/login",
            json={"username": "owner", "password": submitted_password},
        )

    assert response.status_code == 401
    assert submitted_password not in caplog.text
    assert BOOTSTRAP_PASSWORD not in caplog.text
