import pytest

pytestmark = pytest.mark.anyio


async def test_non_api_route_returns_react_index(client_with_static_build) -> None:
    response = await client_with_static_build.get("/profiles/example")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text


async def test_compiled_asset_is_served_from_assets_mount(
    client_with_static_build,
) -> None:
    response = await client_with_static_build.get("/assets/app-hash.js")

    assert response.status_code == 200
    assert 'document.querySelector("#root");' in response.text


@pytest.mark.parametrize("path", ["/api/not-a-route", "/data/private-file"])
async def test_reserved_routes_never_return_the_spa(
    client_with_static_build, path: str
) -> None:
    response = await client_with_static_build.get(path)

    assert response.status_code == 404
    assert '<div id="root"></div>' not in response.text


async def test_data_root_cannot_be_mounted_as_the_static_build(
    test_settings, test_client_factory
) -> None:
    assets_root = test_settings.data_root / "assets"
    assets_root.mkdir()
    (assets_root / "private.txt").write_text("private-data", encoding="utf-8")
    (test_settings.data_root / "index.html").write_text(
        '<div id="root"></div>',
        encoding="utf-8",
    )
    unsafe_settings = test_settings.model_copy(
        update={"static_root": test_settings.data_root}
    )

    async with test_client_factory(unsafe_settings) as unsafe_client:
        asset_response = await unsafe_client.get("/assets/private.txt")
        route_response = await unsafe_client.get("/profiles/example")

    assert asset_response.status_code == 404
    assert "private-data" not in asset_response.text
    assert route_response.status_code == 404
    assert '<div id="root"></div>' not in route_response.text
