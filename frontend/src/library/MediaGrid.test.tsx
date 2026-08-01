import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { MediaGrid } from "./MediaGrid";

const reelFixture = {
  id: "reel-1",
  instagram_media_id: "456",
  shortcode: "REEL123",
  story_media_id: null,
  identity_type: "shortcode",
  identity_value: "REEL123",
  owner_profile_id: "profile-1",
  kind: "reel" as const,
  caption: "",
  accessibility_caption: "A mountain sunrise",
  published_at: "2026-08-01T00:00:00Z",
  original_url: "https://www.instagram.com/reel/REEL123/",
  story_expires_at: null,
  downloaded_at: "2026-08-01T00:01:00Z",
  created_at: "2026-08-01T00:01:00Z",
  updated_at: "2026-08-01T00:01:00Z",
  assets: [
    {
      id: "content-1",
      media_id: "reel-1",
      relative_path: "reel.mp4",
      mime_type: "video/mp4",
      kind: "video" as const,
      role: "content" as const,
      position: 0,
      file_size: 100,
      created_at: "2026-08-01T00:01:00Z",
    },
    {
      id: "poster-1",
      media_id: "reel-1",
      relative_path: "reel.jpg",
      mime_type: "image/jpeg",
      kind: "image" as const,
      role: "poster" as const,
      position: 0,
      file_size: 20,
      created_at: "2026-08-01T00:01:00Z",
    },
  ],
};

const storyFixture = {
  ...reelFixture,
  id: "story-1",
  instagram_media_id: null,
  shortcode: null,
  story_media_id: "3952742051065980676",
  identity_type: "story_media_id",
  identity_value: "3952742051065980676",
  kind: "story" as const,
  original_url:
    "https://www.instagram.com/stories/katerina.soria/3952742051065980676/",
  story_expires_at: "2026-08-02T00:00:00Z",
  assets: [],
};

describe("MediaGrid", () => {
  it("uses a Reel poster without counting it as carousel content", () => {
    render(
      <MemoryRouter>
        <MediaGrid media={[reelFixture]} />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("img", { name: "A mountain sunrise" }),
    ).toHaveAttribute("src", "/api/media/reel-1/assets/poster-1");
    expect(screen.getByText("Reel")).toBeInTheDocument();
    expect(screen.queryByText("2 items")).not.toBeInTheDocument();
  });

  it("labels a Story with its Story media ID when shortcode is null", () => {
    render(
      <MemoryRouter>
        <MediaGrid media={[storyFixture]} />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("link", {
        name: "Open Story 3952742051065980676",
      }),
    ).toBeVisible();
    expect(screen.getByText("Story")).toBeInTheDocument();
  });
});
