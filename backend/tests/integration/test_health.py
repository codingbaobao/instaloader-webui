from fastapi.testclient import TestClient

from instaloader_webui.main import create_app


def test_health_uses_consistent_envelope(test_settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"status": "ok"},
        "error": None,
        "meta": {},
    }
