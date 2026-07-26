from datetime import UTC, datetime
from typing import Annotated
from unicodedata import normalize

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from instaloader_webui.api.dependencies import (
    ApiError,
    RequestSession,
    get_app_secret,
    get_auth_service,
    get_settings,
    require_session_status,
    require_session_status_csrf,
)
from instaloader_webui.api.envelope import ApiEnvelope
from instaloader_webui.auth.app_secret import AppSecret
from instaloader_webui.auth.session_tokens import derive_csrf_token
from instaloader_webui.config import (
    MAXIMUM_USERNAME_BYTES,
    Settings,
)
from instaloader_webui.services.auth_service import (
    AuthenticationBusyError,
    AuthService,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    LoginThrottledError,
)

SESSION_COOKIE_NAME = "iw_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    username: str = Field(min_length=1, max_length=64)
    password: SecretStr

    @field_validator("username", mode="before")
    @classmethod
    def canonicalize_username(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        canonical = normalize("NFKC", value).strip().casefold()
        _validate_utf8_bytes(canonical, maximum_bytes=MAXIMUM_USERNAME_BYTES)
        return canonical

    @field_validator("password")
    @classmethod
    def validate_password_text(cls, value: SecretStr) -> SecretStr:
        _validate_utf8(value.get_secret_value())
        return value


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_password: SecretStr
    new_password: SecretStr

    @field_validator("current_password", "new_password")
    @classmethod
    def validate_password_text(cls, value: SecretStr) -> SecretStr:
        _validate_utf8(value.get_secret_value())
        return value


def _validate_utf8_bytes(value: str, *, maximum_bytes: int) -> None:
    encoded = _validate_utf8(value)
    if len(encoded) > maximum_bytes:
        raise ValueError("credential exceeds the UTF-8 byte limit")


def _validate_utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("credential must be valid UTF-8") from error


def _session_data(
    request_session: RequestSession, app_secret: AppSecret
) -> dict[str, object]:
    return {
        "username": request_session.authenticated.username,
        "must_change_password": request_session.authenticated.must_change_password,
        "expires_at": request_session.authenticated.expires_at.isoformat(),
        "csrf_token": derive_csrf_token(
            request_session.raw_token,
            app_secret.value,
        ),
    }


@router.post("/login", response_model=ApiEnvelope[dict[str, object]])
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    app_secret: Annotated[AppSecret, Depends(get_app_secret)],
) -> ApiEnvelope[dict[str, object]]:
    client_ip = request.client.host if request.client is not None else ""
    try:
        result = auth_service.login(
            payload.username,
            payload.password.get_secret_value(),
            client_ip,
            datetime.now(UTC),
        )
    except LoginThrottledError as error:
        raise ApiError(
            status_code=429,
            code="login_throttled",
            message="Too many login attempts. Try again later.",
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error
    except AuthenticationBusyError as error:
        raise ApiError(
            status_code=503,
            code="authentication_busy",
            message="Authentication is temporarily busy. Try again shortly.",
            headers={"Retry-After": "1"},
        ) from error
    except InvalidCredentialsError as error:
        raise ApiError(
            status_code=401,
            code="invalid_credentials",
            message="The username or password is incorrect.",
        ) from error

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=result.raw_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
    )
    return ApiEnvelope(
        success=True,
        data={
            "username": result.username,
            "must_change_password": result.must_change_password,
            "expires_at": result.expires_at.isoformat(),
            "csrf_token": derive_csrf_token(
                result.raw_token,
                app_secret.value,
            ),
        },
    )


@router.get("/session", response_model=ApiEnvelope[dict[str, object]])
def session(
    request_session: Annotated[RequestSession, Depends(require_session_status)],
    app_secret: Annotated[AppSecret, Depends(get_app_secret)],
) -> ApiEnvelope[dict[str, object]]:
    return ApiEnvelope(
        success=True,
        data=_session_data(request_session, app_secret),
    )


@router.post("/change-password", response_model=ApiEnvelope[dict[str, object]])
def change_password(
    payload: ChangePasswordRequest,
    request_session: Annotated[RequestSession, Depends(require_session_status_csrf)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    app_secret: Annotated[AppSecret, Depends(get_app_secret)],
) -> ApiEnvelope[dict[str, object]]:
    try:
        authenticated = auth_service.change_password(
            request_session.raw_token,
            payload.current_password.get_secret_value(),
            payload.new_password.get_secret_value(),
            datetime.now(UTC),
        )
    except InvalidCurrentPasswordError as error:
        raise ApiError(
            status_code=401,
            code="invalid_current_password",
            message="The current password is incorrect.",
        ) from error
    except InvalidCredentialsError as error:
        raise ApiError(
            status_code=401,
            code="authentication_required",
            message="Authentication is required.",
        ) from error
    except AuthenticationBusyError as error:
        raise ApiError(
            status_code=503,
            code="authentication_busy",
            message="Authentication is temporarily busy. Try again shortly.",
            headers={"Retry-After": "1"},
        ) from error

    updated_session = RequestSession(
        raw_token=request_session.raw_token,
        authenticated=authenticated,
    )
    return ApiEnvelope(
        success=True,
        data=_session_data(updated_session, app_secret),
    )


@router.post("/logout", response_model=ApiEnvelope[dict[str, bool]])
def logout(
    response: Response,
    request_session: Annotated[RequestSession, Depends(require_session_status_csrf)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApiEnvelope[dict[str, bool]]:
    auth_service.logout(request_session.raw_token, datetime.now(UTC))
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return ApiEnvelope(success=True, data={"logged_out": True})
