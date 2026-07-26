from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

_RESERVED_ROUTE_ROOTS = frozenset({"api", "assets", "data"})


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def install_spa(app: FastAPI, static_root: Path, data_root: Path) -> None:
    resolved_data_root = data_root.resolve()
    resolved_static_root = static_root.resolve()
    assets_root = static_root / "assets"
    index_path = static_root / "index.html"
    static_overlaps_data = any(
        _is_within(candidate.resolve(), resolved_data_root)
        for candidate in (resolved_static_root, assets_root, index_path)
    )

    if not static_overlaps_data and assets_root.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=assets_root),
            name="frontend-assets",
        )

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(
        request: Request, error: StarletteHTTPException
    ) -> Response:
        requested_path = request.url.path.lstrip("/")
        route_root = requested_path.partition("/")[0]
        if (
            error.status_code == 404
            and request.method == "GET"
            and route_root not in _RESERVED_ROUTE_ROOTS
            and not static_overlaps_data
            and index_path.is_file()
        ):
            return FileResponse(index_path, media_type="text/html")
        return JSONResponse(
            status_code=error.status_code,
            headers=error.headers,
            content={"detail": error.detail},
        )
