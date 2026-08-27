import json
from datetime import UTC, datetime, timedelta

import pytest
from instaloader.nodeiterator import FrozenNodeIterator
from sqlalchemy import delete, update

from instaloader_webui.db.library_repositories import LibraryRepository
from instaloader_webui.db.models import Profile, ProfileSyncCheckpoint
from instaloader_webui.db.schema import initialize_database
from instaloader_webui.instagram.profile_sync_checkpoints import (
    ProfileSyncCheckpointError,
    ProfileSyncCheckpointRepository,
    decode_frozen_iterator,
    encode_frozen_iterator,
)

NOW = datetime(2026, 8, 28, 1, 2, 3, tzinfo=UTC)
SAFE_CHECKPOINT_ERROR = "Profile sync checkpoint is invalid."


def _frozen(*, total_index: int = 7) -> FrozenNodeIterator:
    return FrozenNodeIterator(
        query_hash="query-hash",
        query_variables={"id": "727", "first": 12, "after": "cursor-value"},
        query_referer="https://www.instagram.com/mihi_727/",
        context_username="mihi_727",
        total_index=total_index,
        best_before=NOW.timestamp(),
        remaining_data={
            "edges": [
                {"node": {"media": {"code": "one", "taken_at": 123}}},
                {"node": {"media": {"code": "two", "taken_at": 456}}},
            ],
            "page_info": {"has_next_page": True, "end_cursor": "next-page"},
        },
        first_node={"media": {"code": "first"}},
        doc_id="doc-id",
    )


def test_frozen_iterator_codec_round_trips_every_resume_field() -> None:
    frozen = _frozen()

    encoded = encode_frozen_iterator(frozen)
    decoded = decode_frozen_iterator(encoded)

    assert decoded == frozen
    assert json.loads(encoded) == {
        "version": 1,
        "query_hash": frozen.query_hash,
        "query_variables": frozen.query_variables,
        "query_referer": frozen.query_referer,
        "context_username": frozen.context_username,
        "total_index": frozen.total_index,
        "best_before": frozen.best_before,
        "remaining_data": frozen.remaining_data,
        "first_node": frozen.first_node,
        "doc_id": frozen.doc_id,
    }


def test_frozen_iterator_codec_rejects_payload_over_two_mib() -> None:
    oversized = _frozen()._replace(first_node={"value": "x" * (2 * 1024 * 1024)})

    with pytest.raises(ProfileSyncCheckpointError, match=SAFE_CHECKPOINT_ERROR):
        encode_frozen_iterator(oversized)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": "field"}),
        lambda value: value.update({"version": 2}),
        lambda value: value.update({"version": True}),
        lambda value: value.update({"query_hash": 42}),
        lambda value: value.update({"query_variables": []}),
        lambda value: value.update({"total_index": True}),
        lambda value: value.update({"total_index": -1}),
        lambda value: value.update({"best_before": "not-a-timestamp"}),
        lambda value: value.update({"remaining_data": []}),
        lambda value: value.update({"first_node": []}),
        lambda value: value.update({"doc_id": {"not": "a string"}}),
    ],
    ids=[
        "unknown-key",
        "wrong-version",
        "boolean-version",
        "wrong-optional-string",
        "wrong-query-variables",
        "boolean-total-index",
        "negative-total-index",
        "wrong-timestamp",
        "wrong-remaining-data",
        "wrong-first-node",
        "wrong-doc-id",
    ],
)
def test_frozen_iterator_codec_rejects_invalid_shapes(mutate) -> None:
    value = json.loads(encode_frozen_iterator(_frozen()))
    mutate(value)

    with pytest.raises(ProfileSyncCheckpointError, match=SAFE_CHECKPOINT_ERROR):
        decode_frozen_iterator(json.dumps(value))


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_frozen_iterator_codec_rejects_non_finite_timestamps(non_finite: float) -> None:
    with pytest.raises(ProfileSyncCheckpointError, match=SAFE_CHECKPOINT_ERROR):
        encode_frozen_iterator(_frozen()._replace(best_before=non_finite))

    encoded = encode_frozen_iterator(_frozen())
    value = json.loads(encoded)
    value["best_before"] = non_finite
    with pytest.raises(ProfileSyncCheckpointError, match=SAFE_CHECKPOINT_ERROR):
        decode_frozen_iterator(json.dumps(value))


@pytest.fixture
def checkpoint_repository(session_factory, test_settings):
    initialize_database(test_settings)
    library = LibraryRepository(session_factory)
    profile = library.upsert_profile_stub(
        username="mihi_727",
        tracked=True,
        now=NOW,
    )
    return ProfileSyncCheckpointRepository(session_factory), library, profile


def test_checkpoint_repository_keeps_posts_and_reels_independent_and_atomic(
    checkpoint_repository,
) -> None:
    checkpoints, _library, profile = checkpoint_repository
    posts = _frozen(total_index=10)
    reels = _frozen(total_index=20)._replace(doc_id="reels-doc")

    assert checkpoints.get(profile.id, "posts") is None
    checkpoints.save_frozen(
        profile_id=profile.id,
        source="posts",
        frozen=posts,
        now=NOW,
    )
    checkpoints.save_frozen(
        profile_id=profile.id,
        source="reels",
        frozen=reels,
        now=NOW + timedelta(seconds=1),
    )

    posts_snapshot = checkpoints.get(profile.id, "posts")
    reels_snapshot = checkpoints.get(profile.id, "reels")
    assert posts_snapshot is not None
    assert posts_snapshot.frozen == posts
    assert posts_snapshot.backfill_complete is False
    assert reels_snapshot is not None
    assert reels_snapshot.frozen == reels

    replacement = posts._replace(total_index=99)
    checkpoints.save_frozen(
        profile_id=profile.id,
        source="posts",
        frozen=replacement,
        now=NOW + timedelta(seconds=2),
    )
    assert checkpoints.get(profile.id, "posts").frozen == replacement
    assert checkpoints.get(profile.id, "reels").frozen == reels


def test_checkpoint_completion_clears_cursor_and_reset_restarts_backfill(
    checkpoint_repository,
) -> None:
    checkpoints, _library, profile = checkpoint_repository
    checkpoints.save_frozen(
        profile_id=profile.id,
        source="posts",
        frozen=_frozen(),
        now=NOW,
    )

    checkpoints.mark_complete(
        profile_id=profile.id,
        source="posts",
        now=NOW + timedelta(seconds=1),
    )
    completed = checkpoints.get(profile.id, "posts")
    assert completed is not None
    assert completed.cursor_version == 1
    assert completed.frozen is None
    assert completed.backfill_complete is True

    checkpoints.reset(
        profile_id=profile.id,
        source="posts",
        now=NOW + timedelta(seconds=2),
    )
    reset = checkpoints.get(profile.id, "posts")
    assert reset is not None
    assert reset.frozen is None
    assert reset.backfill_complete is False


def test_checkpoint_rows_cascade_when_profile_is_deleted(
    checkpoint_repository,
    session_factory,
) -> None:
    checkpoints, _library, profile = checkpoint_repository
    checkpoints.save_frozen(
        profile_id=profile.id,
        source="posts",
        frozen=_frozen(),
        now=NOW,
    )
    with session_factory.begin() as session:
        session.execute(delete(Profile).where(Profile.id == profile.id))

    with session_factory() as session:
        assert session.get(ProfileSyncCheckpoint, (profile.id, "posts")) is None


def test_checkpoint_repository_hides_corrupt_cursor_content(
    checkpoint_repository,
    session_factory,
) -> None:
    checkpoints, _library, profile = checkpoint_repository
    checkpoints.save_frozen(
        profile_id=profile.id,
        source="posts",
        frozen=_frozen(),
        now=NOW,
    )
    with session_factory.begin() as session:
        session.execute(
            update(ProfileSyncCheckpoint)
            .where(
                ProfileSyncCheckpoint.profile_id == profile.id,
                ProfileSyncCheckpoint.source == "posts",
            )
            .values(cursor_json='{"sessionid":"must-not-leak"}')
        )

    with pytest.raises(ProfileSyncCheckpointError) as caught:
        checkpoints.get(profile.id, "posts")

    assert str(caught.value) == SAFE_CHECKPOINT_ERROR
    assert "sessionid" not in str(caught.value)
