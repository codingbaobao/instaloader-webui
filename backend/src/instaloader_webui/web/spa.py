from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

_ROOT_STATIC_FILES: Final = (
    ("favicon.svg", "image/svg+xml"),
    ("favicon.ico", "image/x-icon"),
    ("favicon-16.png", "image/png"),
    ("favicon-32.png", "image/png"),
    ("site.webmanifest", "application/manifest+json"),
)
_STATIC_DIRECTORY_MOUNTS: Final = (
    ("assets", "frontend-assets"),
    ("brand", "frontend-brand"),
    ("icons", "frontend-icons"),
)
_RESERVED_ROUTE_ROOTS = frozenset(
    {
        "api",
        "data",
        *(route_root for route_root, _route_name in _STATIC_DIRECTORY_MOUNTS),
        *(filename for filename, _media_type in _ROOT_STATIC_FILES),
    }
)


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _paths_overlap(first: Path, second: Path) -> bool:
    return _is_within(first, second) or _is_within(second, first)


def _is_safe_static_path(
    candidate: Path,
    resolved_static_root: Path,
    resolved_data_root: Path,
    static_overlaps_data: bool,
) -> bool:
    resolved_candidate = candidate.resolve()
    return (
        not static_overlaps_data
        and _is_within(resolved_candidate, resolved_static_root)
        and not _paths_overlap(resolved_candidate, resolved_data_root)
    )


def _root_static_file_endpoint(
    file_path: Path,
    media_type: str,
    resolved_static_root: Path,
    resolved_data_root: Path,
    static_overlaps_data: bool,
) -> Callable[[], Awaitable[Response]]:
    async def serve_root_static_file() -> Response:
        if not file_path.is_file() or not _is_safe_static_path(
            file_path,
            resolved_static_root,
            resolved_data_root,
            static_overlaps_data,
        ):
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        return FileResponse(file_path, media_type=media_type)

    return serve_root_static_file


def install_spa(app: FastAPI, static_root: Path, data_root: Path) -> None:
    resolved_data_root = data_root.resolve()
    resolved_static_root = static_root.resolve()
    assets_root = static_root / "assets"
    index_path = static_root / "index.html"
    static_overlaps_data = any(
        _paths_overlap(candidate.resolve(), resolved_data_root)
        for candidate in (resolved_static_root, assets_root, index_path)
    )

    for route_root, route_name in _STATIC_DIRECTORY_MOUNTS:
        directory = static_root / route_root
        if directory.is_dir() and _is_safe_static_path(
            directory,
            resolved_static_root,
            resolved_data_root,
            static_overlaps_data,
        ):
            app.mount(
                f"/{route_root}",
                StaticFiles(directory=directory),
                name=route_name,
            )

    for filename, media_type in _ROOT_STATIC_FILES:
        file_path = static_root / filename
        app.add_api_route(
            f"/{filename}",
            _root_static_file_endpoint(
                file_path,
                media_type,
                resolved_static_root,
                resolved_data_root,
                static_overlaps_data,
            ),
            methods=["GET"],
            include_in_schema=False,
            name=f"frontend-{filename}",
        )

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request: Request, error: StarletteHTTPException) -> Response:
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
