"""Authenticated Instaloader boundary for all-or-nothing followee discovery."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from instaloader import InstaloaderException, Profile

from instaloader_webui.db.followee_import_repositories import DiscoveredFollowee
from instaloader_webui.instagram.errors import (
    SESSION_REJECTED,
    TRANSIENT,
    classify_instaloader_error,
)
from instaloader_webui.instagram.profile_lookup import ProfileLookupResolver
from instaloader_webui.instagram.session_store import InstagramSessionStoreError
from instaloader_webui.instagram.worker_runtime import (
    InstagramSessionRevisionError,
    WorkerInstaloaderRuntime,
)

ProgressCallback = Callable[[int, int | None, str], None]


class FolloweeDiscoveryError(RuntimeError):
    """A concise user-safe failure from authenticated followee discovery."""


class FolloweeDiscoveryAdapter:
    """Read a logged-in account's followees without persisting partial results."""

    def __init__(
        self,
        *,
        jobs_root: Path,
        loader_runtime: WorkerInstaloaderRuntime,
        profile_lookup_resolver: ProfileLookupResolver,
        progress: ProgressCallback,
    ) -> None:
        self._jobs_root = jobs_root.resolve()
        self._loader_runtime = loader_runtime
        self._profile_lookup_resolver = profile_lookup_resolver
        self._progress = progress

    def discover(
        self,
        *,
        source_username: str,
        session_imported_at: datetime,
    ) -> tuple[DiscoveredFollowee, ...]:
        staging_directory = self._jobs_root / "followee-discovery"
        staging_directory.mkdir(parents=True, exist_ok=True)
        try:
            loader = self._loader_runtime.acquire_authenticated(
                staging_directory,
                expected_username=source_username,
                expected_imported_at=session_imported_at,
            )
        except InstagramSessionRevisionError as error:
            raise FolloweeDiscoveryError(str(error)) from error
        except InstagramSessionStoreError:
            raise FolloweeDiscoveryError(
                "Instagram session storage is unreadable. Re-import the Cookie file."
            ) from None

        try:
            logged_in_username = loader.test_login()
            if (
                logged_in_username is None
                or logged_in_username.casefold() != source_username.casefold()
            ):
                raise FolloweeDiscoveryError(SESSION_REJECTED)
            source_profile = self._profile_lookup_resolver.resolve(
                loader.context,
                logged_in_username,
            )
            total = _followee_count(source_profile)
            self._progress(0, total, "Reading Instagram followees.")
            discovered: list[DiscoveredFollowee] = []
            for current, profile in enumerate(
                source_profile.get_followees(),
                start=1,
            ):
                discovered.append(_normalize_followee(profile))
                self._progress(
                    current,
                    total,
                    f"Discovered {current} Instagram followees.",
                )
            return tuple(discovered)
        except FolloweeDiscoveryError:
            raise
        except InstaloaderException as error:
            raise FolloweeDiscoveryError(
                classify_instaloader_error(
                    error,
                    session_configured=True,
                    target="profile",
                )
            ) from error
        except Exception as error:
            if error.__class__.__module__.startswith("instaloader"):
                message = classify_instaloader_error(
                    error,
                    session_configured=True,
                    target="profile",
                )
            else:
                message = TRANSIENT
            raise FolloweeDiscoveryError(message) from error


def _followee_count(profile: Profile) -> int | None:
    try:
        count = profile.followees
    except Exception:  # noqa: BLE001 - Instaloader properties may raise dynamically.
        return None
    return count if isinstance(count, int) and count >= 0 else None


def _normalize_followee(profile: Profile) -> DiscoveredFollowee:
    try:
        instagram_user_id = str(profile.userid)
        username = profile.username
        full_name = profile.full_name or ""
        profile_pic = profile.profile_pic_url
        is_private = profile.is_private
    except Exception:  # noqa: BLE001 - normalize all malformed upstream objects.
        raise FolloweeDiscoveryError(
            "Instagram returned invalid followee data."
        ) from None
    if (
        not instagram_user_id
        or not isinstance(username, str)
        or not username
        or len(username) > 64
        or not isinstance(full_name, str)
        or not isinstance(is_private, bool)
    ):
        raise FolloweeDiscoveryError(
            "Instagram returned invalid followee data."
        )
    return DiscoveredFollowee(
        instagram_user_id=instagram_user_id,
        username=username,
        full_name=full_name,
        profile_pic_url=str(profile_pic) if profile_pic is not None else None,
        is_private=is_private,
    )
