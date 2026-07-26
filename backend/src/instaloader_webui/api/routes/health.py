from fastapi import APIRouter

from instaloader_webui.api.envelope import ApiEnvelope

router = APIRouter(prefix="/api")


@router.get("/health", response_model=ApiEnvelope[dict[str, str]])
def health() -> ApiEnvelope[dict[str, str]]:
    return ApiEnvelope(success=True, data={"status": "ok"})
