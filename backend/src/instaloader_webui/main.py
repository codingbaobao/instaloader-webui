from fastapi import FastAPI

from instaloader_webui.api.routes.health import router as health_router
from instaloader_webui.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    app = FastAPI(title="Instaloader WebUI")
    app.state.settings = resolved
    app.include_router(health_router)
    return app
