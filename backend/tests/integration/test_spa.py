import pytest

pytestmark = pytest.mark.anyio


async def test_non_api_route_returns_react_index(client_with_static_build) -> None:
    response = await client_with_static_build.get("/profiles/example")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "cache-control" not in response.headers
    assert "strict-transport-security" not in response.headers
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


async def test_static_build_is_disabled_when_data_root_is_nested_beneath_it(
    test_settings, test_client_factory, tmp_path
) -> None:
    static_root = tmp_path / "static-parent"
    assets_root = static_root / "assets"
    data_root = static_root / "stored-data"
    assets_root.mkdir(parents=True)
    data_root.mkdir()
    (static_root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (assets_root / "app.js").write_text("public-app", encoding="utf-8")
    (data_root / "private.txt").write_text("private-data", encoding="utf-8")
    unsafe_settings = test_settings.model_copy(
        update={"static_root": static_root, "data_root": data_root}
    )

    async with test_client_factory(unsafe_settings) as unsafe_client:
        asset_response = await unsafe_client.get("/assets/app.js")
        route_response = await unsafe_client.get("/profiles/example")

    assert asset_response.status_code == 404
    assert "public-app" not in asset_response.text
    assert route_response.status_code == 404
    assert '<div id="root"></div>' not in route_response.text


async def test_static_build_is_disabled_when_data_root_is_nested_under_assets(
    test_settings, test_client_factory, tmp_path
) -> None:
    static_root = tmp_path / "static-assets-parent"
    data_root = static_root / "assets" / "stored-data"
    data_root.mkdir(parents=True)
    (static_root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (data_root / "private.txt").write_text("private-data", encoding="utf-8")
    unsafe_settings = test_settings.model_copy(
        update={"static_root": static_root, "data_root": data_root}
    )

    async with test_client_factory(unsafe_settings) as unsafe_client:
        private_response = await unsafe_client.get("/assets/stored-data/private.txt")

    assert private_response.status_code == 404
    assert "private-data" not in private_response.text
