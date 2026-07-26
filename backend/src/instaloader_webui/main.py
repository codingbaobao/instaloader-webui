from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from instaloader_webui.api.dependencies import ApiError
from instaloader_webui.api.middleware import (
    RequestBodyLimitMiddleware,
    SafeExceptionMiddleware,
    SecurityHeadersMiddleware,
)
from instaloader_webui.api.routes.auth import (
    SESSION_COOKIE_NAME,
)
from instaloader_webui.api.routes.auth import (
    router as auth_router,
)
from instaloader_webui.api.routes.health import router as health_router
from instaloader_webui.auth.app_secret import load_or_create_app_secret
from instaloader_webui.auth.passwords import PasswordService
from instaloader_webui.auth.throttle import LoginThrottle
from instaloader_webui.config import Settings
from instaloader_webui.db.engine import build_engine, build_session_factory
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.db.repositories import (
    AdminRepository,
    LoginFailureRepository,
    WebSessionRepository,
)
from instaloader_webui.services.admin_bootstrap import bootstrap_admin
from instaloader_webui.services.auth_service import AuthService
from instaloader_webui.web.spa import install_spa


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        run_migrations(resolved)
        app_secret = load_or_create_app_secret(resolved.data_root)
        engine = build_engine(resolved.database_path)
        session_factory = build_session_factory(engine)
        passwords = PasswordService()
        bootstrap_admin(session_factory, resolved, passwords)
        app.state.auth_service = AuthService(
            administrators=AdminRepository(session_factory),
            sessions=WebSessionRepository(session_factory),
            throttle=LoginThrottle(
                repository=LoginFailureRepository(session_factory),
                hmac_secret=app_secret.value,
            ),
            passwords=passwords,
        )
        app.state.app_secret = app_secret
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Instaloader WebUI", lifespan=lifespan)
    app.state.settings = resolved

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        response = JSONResponse(
            status_code=error.status_code,
            headers=error.headers,
            content={
                "success": False,
                "data": None,
                "error": {"code": error.code, "message": error.message},
                "meta": {},
            },
        )
        if (
            error.code == "authentication_required"
            and SESSION_COOKIE_NAME in request.cookies
        ):
            response.delete_cookie(
                key=SESSION_COOKIE_NAME,
                path="/",
                secure=resolved.session_cookie_secure,
                httponly=True,
                samesite="lax",
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": "validation_error",
                    "message": "The request data is invalid.",
                },
                "meta": {},
            },
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    install_spa(app, resolved.static_root, resolved.data_root)
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(SafeExceptionMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    return app
