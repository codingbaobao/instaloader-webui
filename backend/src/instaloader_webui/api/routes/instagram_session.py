"""Administrator API for one encrypted Instagram Cookie session."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from starlette.concurrency import run_in_threadpool

from instaloader_webui.api.dependencies import (
    ApiError,
    get_instagram_session_service,
    require_csrf,
    require_password_change_complete,
)
from instaloader_webui.api.envelope import ApiEnvelope
from instaloader_webui.api.library_dtos import (
    InstagramSessionStatusResponse,
    serialize_instagram_session_status,
)
from instaloader_webui.instagram.cookie_file import MAXIMUM_COOKIE_FILE_BYTES
from instaloader_webui.instagram.session_service import (
    InstagramSessionImportError,
    InstagramSessionService,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_UPLOAD_CHUNK_BYTES = 64 * 1024


@router.get(
    "/instagram-session",
    response_model=ApiEnvelope[InstagramSessionStatusResponse],
)
def get_instagram_session(
    service: Annotated[InstagramSessionService, Depends(get_instagram_session_service)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> ApiEnvelope[InstagramSessionStatusResponse]:
    return ApiEnvelope(
        success=True,
        data=serialize_instagram_session_status(service.status()),
    )


@router.post(
    "/instagram-session",
    response_model=ApiEnvelope[InstagramSessionStatusResponse],
)
async def import_instagram_session(
    cookie_file: Annotated[UploadFile, File()],
    service: Annotated[InstagramSessionService, Depends(get_instagram_session_service)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[InstagramSessionStatusResponse]:
    payload = await _read_cookie_file(cookie_file)
    try:
        status = await run_in_threadpool(
            service.import_netscape,
            payload,
            datetime.now(UTC),
        )
    except InstagramSessionImportError as error:
        raise ApiError(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
        ) from error
    return ApiEnvelope(success=True, data=serialize_instagram_session_status(status))


@router.delete(
    "/instagram-session",
    response_model=ApiEnvelope[InstagramSessionStatusResponse],
)
def remove_instagram_session(
    service: Annotated[InstagramSessionService, Depends(get_instagram_session_service)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[InstagramSessionStatusResponse]:
    service.remove()
    return ApiEnvelope(
        success=True,
        data=InstagramSessionStatusResponse(
            configured=False,
            username=None,
            imported_at=None,
            last_validated_at=None,
        ),
    )


async def _read_cookie_file(cookie_file: UploadFile) -> bytes:
    payload = bytearray()
    try:
        while True:
            chunk = await cookie_file.read(_UPLOAD_CHUNK_BYTES)
            if not chunk:
                return bytes(payload)
            payload.extend(chunk)
            if len(payload) > MAXIMUM_COOKIE_FILE_BYTES:
                raise ApiError(
                    status_code=400,
                    code="invalid_cookie_file",
                    message="The Cookie file is too large.",
                )
    finally:
        await cookie_file.close()
