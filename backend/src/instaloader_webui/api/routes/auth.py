from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from instaloader_webui.api.dependencies import (
    ApiError,
    RequestSession,
    get_auth_service,
    get_settings,
    require_authenticated_session,
    require_csrf,
)
from instaloader_webui.api.envelope import ApiEnvelope
from instaloader_webui.auth.session_tokens import derive_csrf_token
from instaloader_webui.config import MINIMUM_ADMIN_PASSWORD_LENGTH, Settings
from instaloader_webui.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    LoginThrottledError,
    PasswordUnchangedError,
)

SESSION_COOKIE_NAME = "iw_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_password: SecretStr = Field(min_length=1)
    new_password: SecretStr = Field(min_length=MINIMUM_ADMIN_PASSWORD_LENGTH)


def _session_data(
    request_session: RequestSession, settings: Settings
) -> dict[str, object]:
    return {
        "username": request_session.authenticated.username,
        "must_change_password": request_session.authenticated.must_change_password,
        "expires_at": request_session.authenticated.expires_at.isoformat(),
        "csrf_token": derive_csrf_token(
            request_session.raw_token,
            settings.app_secret_key.get_secret_value(),
        ),
    }


@router.post("/login", response_model=ApiEnvelope[dict[str, object]])
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
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
                settings.app_secret_key.get_secret_value(),
            ),
        },
    )


@router.get("/session", response_model=ApiEnvelope[dict[str, object]])
def session(
    request_session: Annotated[
        RequestSession, Depends(require_authenticated_session)
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApiEnvelope[dict[str, object]]:
    return ApiEnvelope(
        success=True,
        data=_session_data(request_session, settings),
    )


@router.post("/change-password", response_model=ApiEnvelope[dict[str, object]])
def change_password(
    payload: ChangePasswordRequest,
    request_session: Annotated[RequestSession, Depends(require_csrf)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
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
    except PasswordUnchangedError as error:
        raise ApiError(
            status_code=422,
            code="password_unchanged",
            message="The new password must be different.",
        ) from error

    updated_session = RequestSession(
        raw_token=request_session.raw_token,
        authenticated=authenticated,
    )
    return ApiEnvelope(
        success=True,
        data=_session_data(updated_session, settings),
    )


@router.post("/logout", response_model=ApiEnvelope[dict[str, bool]])
def logout(
    response: Response,
    request_session: Annotated[RequestSession, Depends(require_csrf)],
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
