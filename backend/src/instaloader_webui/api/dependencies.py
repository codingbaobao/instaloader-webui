import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request

from instaloader_webui.auth.session_tokens import derive_csrf_token
from instaloader_webui.config import Settings
from instaloader_webui.services.auth_service import (
    AuthenticatedSession,
    AuthService,
)


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


def require_authenticated_session(
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


def require_csrf(
    request_session: Annotated[
        RequestSession, Depends(require_authenticated_session)
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    submitted_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> RequestSession:
    expected_token = derive_csrf_token(
        request_session.raw_token,
        settings.app_secret_key.get_secret_value(),
    )
    if not hmac.compare_digest(submitted_token or "", expected_token):
        raise ApiError(
            status_code=403,
            code="csrf_invalid",
            message="The CSRF token is invalid.",
        )
    return request_session
