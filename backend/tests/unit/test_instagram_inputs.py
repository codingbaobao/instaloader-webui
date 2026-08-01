from __future__ import annotations

import pytest

from instaloader_webui.services.instagram_inputs import (
    InvalidInstagramInput,
    PostInput,
    ProfileInput,
    ReelInput,
    StoryInput,
    parse_instagram_input,
)


@pytest.mark.parametrize(
    ("raw", "expected_type", "identifier"),
    [
        ("@natgeo", ProfileInput, "natgeo"),
        (
            "https://www.instagram.com/p/CmzV2H-rrlI/?utm_source=x",
            PostInput,
            "CmzV2H-rrlI",
        ),
        ("https://www.instagram.com/reel/DOqEJyxCRGJ/", ReelInput, "DOqEJyxCRGJ"),
        ("https://www.instagram.com/tv/ABC_123/", PostInput, "ABC_123"),
        (
            (
                "https://www.instagram.com/stories/katerina.soria/"
                "3952742051065980676?utm_source=ig_story_item_share&igsh=secret"
            ),
            StoryInput,
            "3952742051065980676",
        ),
    ],
)
def test_parse_instagram_input_returns_typed_canonical_values(
    raw: str,
    expected_type: type[object],
    identifier: str,
) -> None:
    parsed = parse_instagram_input(raw)

    assert isinstance(parsed, expected_type)
    assert parsed.identifier == identifier
    assert "?" not in (parsed.canonical_url or "")


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.instagram.com/stories/not-valid!/3952742051065980676/",
        "https://www.instagram.com/stories/katerina.soria/not-a-number/",
        "https://www.instagram.com/stories/katerina.soria/123456789012345678901234567890123/",
        "http://www.instagram.com/p/CmzV2H-rrlI/",
        "https://instagram.example/p/CmzV2H-rrlI/",
        "https://user:password@www.instagram.com/p/CmzV2H-rrlI/",
        "https://www.instagram.com/p/CmzV2H-rrlI/extra/",
    ],
)
def test_parse_instagram_input_rejects_unsupported_values_with_safe_error(
    raw: str,
) -> None:
    with pytest.raises(
        InvalidInstagramInput,
        match="^Enter a profile, post, reel, TV, or Story URL\\.$",
    ):
        parse_instagram_input(raw)


def test_parse_instagram_input_normalizes_tv_to_post_url() -> None:
    parsed = parse_instagram_input("https://www.instagram.com/tv/ABC_123/?igsh=secret")

    assert parsed == PostInput(
        shortcode="ABC_123",
        canonical_url="https://www.instagram.com/p/ABC_123/",
    )


def test_parse_instagram_input_normalizes_story_url() -> None:
    parsed = parse_instagram_input(
        "https://www.instagram.com/stories/katerina.soria/3952742051065980676?igsh=secret"
    )

    assert parsed == StoryInput(
        username="katerina.soria",
        story_media_id="3952742051065980676",
        canonical_url=(
            "https://www.instagram.com/stories/"
            "katerina.soria/3952742051065980676/"
        ),
    )
