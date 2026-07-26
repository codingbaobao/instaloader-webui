import asyncio

from httpx import ASGITransport, AsyncClient

from instaloader_webui.main import create_app


async def request_health(test_settings) -> object:
    transport = ASGITransport(app=create_app(test_settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/health")


def test_health_uses_consistent_envelope(test_settings) -> None:
    response = asyncio.run(request_health(test_settings))

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"status": "ok"},
        "error": None,
        "meta": {},
    }
