import hmac
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request

from instaloader_webui.auth.app_secret import AppSecret
from instaloader_webui.auth.session_tokens import derive_csrf_token
from instaloader_webui.config import Settings
from instaloader_webui.db.library_repositories import (
    JobRepository,
    LibraryRepository,
    SettingsRepository,
)
from instaloader_webui.services.auth_service import (
    AuthenticatedSession,
    AuthService,
)
from instaloader_webui.services.library_service import LibraryService


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RequestSession:
    raw_token: str = field(repr=False)
    authenticated: AuthenticatedSession


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_library_repository(request: Request) -> LibraryRepository:
    return request.app.state.library_repository


def get_job_repository(request: Request) -> JobRepository:
    return request.app.state.job_repository


def get_settings_repository(request: Request) -> SettingsRepository:
    return request.app.state.settings_repository


def get_library_service(request: Request) -> LibraryService:
    return request.app.state.library_service


def get_app_secret(request: Request) -> AppSecret:
    return request.app.state.app_secret


def require_session_status(
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    raw_token: Annotated[str | None, Cookie(alias="iw_session")] = None,
) -> RequestSession:
    if raw_token is None:
        raise ApiError(
            status_code=401,
            code="authentication_required",
            message="Authentication is required.",
        )
    authenticated = auth_service.authenticate_session(raw_token, datetime.now(UTC))
    if authenticated is None:
        raise ApiError(
            status_code=401,
            code="authentication_required",
            message="Authentication is required.",
        )
    return RequestSession(raw_token=raw_token, authenticated=authenticated)


def require_authenticated_session(
    request_session: Annotated[RequestSession, Depends(require_session_status)],
) -> RequestSession:
    if request_session.authenticated.must_change_password:
        raise ApiError(
            status_code=403,
            code="password_change_required",
            message="The administrator password must be changed.",
        )
    return request_session


def _validate_csrf(
    request_session: RequestSession,
    app_secret: AppSecret,
    submitted_token: str | None,
) -> RequestSession:
    expected_token = derive_csrf_token(
        request_session.raw_token,
        app_secret.value,
    )
    submitted_bytes = (
        submitted_token.encode("ascii")
        if submitted_token is not None
        and re.fullmatch(r"[0-9a-f]{64}", submitted_token) is not None
        else None
    )
    if submitted_bytes is None or not hmac.compare_digest(
        submitted_bytes, expected_token.encode("ascii")
    ):
        raise ApiError(
            status_code=403,
            code="csrf_invalid",
            message="The CSRF token is invalid.",
        )
    return request_session


def require_csrf(
    request_session: Annotated[RequestSession, Depends(require_authenticated_session)],
    app_secret: Annotated[AppSecret, Depends(get_app_secret)],
    submitted_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> RequestSession:
    return _validate_csrf(request_session, app_secret, submitted_token)


def require_session_status_csrf(
    request_session: Annotated[RequestSession, Depends(require_session_status)],
    app_secret: Annotated[AppSecret, Depends(get_app_secret)],
    submitted_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> RequestSession:
    return _validate_csrf(request_session, app_secret, submitted_token)


def require_password_change_complete(
    request_session: Annotated[RequestSession, Depends(require_authenticated_session)],
) -> RequestSession:
    return request_session
