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
from instaloader_webui.api.routes.followee_imports import (
    router as followee_imports_router,
)
from instaloader_webui.api.routes.health import router as health_router
from instaloader_webui.api.routes.instagram_session import (
    router as instagram_session_router,
)
from instaloader_webui.api.routes.jobs import router as jobs_router
from instaloader_webui.api.routes.media import router as media_router
from instaloader_webui.api.routes.profiles import router as profiles_router
from instaloader_webui.api.routes.settings import router as settings_router
from instaloader_webui.auth.app_secret import load_or_create_app_secret
from instaloader_webui.auth.passwords import PasswordService
from instaloader_webui.auth.throttle import LoginThrottle
from instaloader_webui.config import Settings
from instaloader_webui.db.engine import build_engine, build_session_factory
from instaloader_webui.db.followee_import_repositories import (
    FolloweeImportRepository,
)
from instaloader_webui.db.library_repositories import (
    JobRepository,
    LibraryRepository,
    SettingsRepository,
)
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.db.repositories import (
    AdminRepository,
    LoginFailureRepository,
    WebSessionRepository,
)
from instaloader_webui.instagram.session_service import InstagramSessionService
from instaloader_webui.instagram.session_store import InstagramSessionStore
from instaloader_webui.services.admin_bootstrap import bootstrap_admin
from instaloader_webui.services.auth_service import AuthService
from instaloader_webui.services.followee_import_service import FolloweeImportService
from instaloader_webui.services.library_service import LibraryService
from instaloader_webui.web.spa import install_spa


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        run_migrations(resolved)
        resolved.media_root.mkdir(parents=True, exist_ok=True)
        resolved.jobs_root.mkdir(parents=True, exist_ok=True)
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
        app.state.library_repository = LibraryRepository(session_factory)
        app.state.job_repository = JobRepository(session_factory)
        app.state.settings_repository = SettingsRepository(session_factory)
        app.state.library_service = LibraryService(
            library=app.state.library_repository,
            jobs=app.state.job_repository,
        )
        app.state.app_secret = app_secret
        app.state.instagram_session_store = InstagramSessionStore(
            resolved.data_root,
            app_secret,
        )
        app.state.instagram_session_service = InstagramSessionService(
            app.state.instagram_session_store,
        )
        app.state.followee_import_repository = FolloweeImportRepository(
            session_factory
        )
        app.state.followee_import_service = FolloweeImportService(
            repository=app.state.followee_import_repository,
            sessions=app.state.instagram_session_store,
        )
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
    app.include_router(profiles_router)
    app.include_router(media_router)
    app.include_router(jobs_router)
    app.include_router(settings_router)
    app.include_router(instagram_session_router)
    app.include_router(followee_imports_router)
    install_spa(app, resolved.static_root, resolved.data_root)
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(SafeExceptionMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    return app
