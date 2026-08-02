from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from instaloader import Profile

from instaloader_webui.db.followee_import_repositories import DiscoveredFollowee
from instaloader_webui.instagram.followee_discovery import FolloweeDiscoveryAdapter
from instaloader_webui.instagram.profile_lookup import ProfileLookupResolver
from instaloader_webui.instagram.worker_runtime import WorkerInstaloaderRuntime

NOW = datetime(2026, 8, 2, 8, 0, tzinfo=UTC)


def test_followee_discovery_resolves_source_profile_through_shared_gateway(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: bypassing the gateway restores the rate-limited upstream
    # Profile.from_username() path for followee discovery.
    followee = SimpleNamespace(
        userid=314159,
        username="followed.account",
        full_name="Followed Account",
        profile_pic_url="https://cdn.example/followed.jpg",
        is_private=False,
    )
    source_profile = SimpleNamespace(
        followees=1,
        get_followees=lambda: (followee,),
    )
    context = object()

    class Loader:
        def __init__(self) -> None:
            self.context = context

        def test_login(self) -> str:
            return "Source.Account"

    class Runtime:
        def acquire_authenticated(
            self,
            _staging_directory,
            *,
            expected_username: str,
            expected_imported_at: datetime,
        ) -> Loader:
            assert expected_username == "source.account"
            assert expected_imported_at == NOW
            return Loader()

    class Resolver:
        def resolve(self, supplied_context: object, username: str) -> object:
            if supplied_context is not context or username != "Source.Account":
                raise AssertionError(
                    "followee discovery supplied the wrong lookup input"
                )
            return source_profile

    monkeypatch.setattr(
        Profile,
        "from_username",
        staticmethod(
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("followee discovery bypassed the profile gateway")
            )
        ),
    )
    adapter = FolloweeDiscoveryAdapter(
        jobs_root=tmp_path,
        loader_runtime=cast(WorkerInstaloaderRuntime, cast(object, Runtime())),
        profile_lookup_resolver=cast(
            ProfileLookupResolver,
            cast(object, Resolver()),
        ),
        progress=lambda *_args: None,
    )

    discovered = adapter.discover(
        source_username="source.account",
        session_imported_at=NOW,
    )

    assert discovered == (
        DiscoveredFollowee(
            instagram_user_id="314159",
            username="followed.account",
            full_name="Followed Account",
            profile_pic_url="https://cdn.example/followed.jpg",
            is_private=False,
        ),
    )
