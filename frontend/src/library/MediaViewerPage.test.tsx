import { HttpResponse, http } from "msw";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";

const authenticatedSession = {
  username: "owner",
  must_change_password: false,
  expires_at: "2026-08-02T00:00:00Z",
  csrf_token: "c".repeat(64),
};

const profileFixture = {
  id: "profile-1",
  instagram_user_id: "123",
  username: "katerina.soria",
  full_name: "Katerina Soria",
  biography: "Photographer",
  profile_pic_url: null,
  tracked: true,
  status: "active",
  last_sync_attempted_at: null,
  last_sync_succeeded_at: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  media_count: 1,
};

const reelFixture = {
  id: "reel-1",
  instagram_media_id: "456",
  shortcode: "REEL123",
  story_media_id: null,
  identity_type: "shortcode",
  identity_value: "REEL123",
  owner_profile_id: "profile-1",
  kind: "reel",
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
      kind: "video",
      role: "content",
      position: 0,
      file_size: 100,
      created_at: "2026-08-01T00:01:00Z",
    },
    {
      id: "poster-1",
      media_id: "reel-1",
      relative_path: "reel.jpg",
      mime_type: "image/jpeg",
      kind: "image",
      role: "poster",
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
  kind: "story",
  original_url:
    "https://www.instagram.com/stories/katerina.soria/3952742051065980676/",
  story_expires_at: "2026-08-02T00:00:00Z",
  assets: [
    {
      ...reelFixture.assets[0],
      id: "story-content-1",
      media_id: "story-1",
    },
  ],
};

function successEnvelope<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

function renderViewer(media: typeof reelFixture | typeof storyFixture) {
  server.use(
    http.get(`/api/media/${media.id}`, () =>
      HttpResponse.json(successEnvelope(media)),
    ),
    http.get("/api/profiles/profile-1", () =>
      HttpResponse.json(successEnvelope(profileFixture)),
    ),
  );
  return render(
    <TestRouter
      initialPath={`/media/${media.id}`}
      initialSession={authenticatedSession}
    />,
  );
}

describe("MediaViewerPage", () => {
  it("shows one Reel content video with its matching poster and no carousel", async () => {
    const { container } = renderViewer(reelFixture);

    expect(
      await screen.findByRole("heading", { name: "Reel" }),
    ).toBeVisible();
    const video = container.querySelector("video");
    expect(video).toHaveAttribute(
      "src",
      "/api/media/reel-1/assets/content-1",
    );
    expect(video).toHaveAttribute(
      "poster",
      "/api/media/reel-1/assets/poster-1",
    );
    expect(
      screen.queryByLabelText("Carousel controls"),
    ).not.toBeInTheDocument();
  });

  it("shows a Story with a null shortcode and its canonical original link", async () => {
    renderViewer(storyFixture);

    expect(
      await screen.findByRole("heading", { name: "Story" }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Open original on Instagram" }),
    ).toHaveAttribute(
      "href",
      "https://www.instagram.com/stories/katerina.soria/3952742051065980676/",
    );
    expect(screen.getByText(/3952742051065980676/)).toBeVisible();
  });

  it("renders icon-only media actions with accessible names", async () => {
    renderViewer(reelFixture);

    expect(
      await screen.findByRole("heading", { name: "Reel" }),
    ).toBeVisible();
    const actions = screen.getByRole("group", { name: "Media actions" });
    const instagramLink = within(actions).getByRole("link", {
      name: "Open original on Instagram",
    });
    expect(instagramLink).toHaveAttribute(
      "href",
      "https://www.instagram.com/reel/REEL123/",
    );
    expect(instagramLink).toHaveAttribute("target", "_blank");
    expect(instagramLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(instagramLink).toHaveAttribute(
      "data-tooltip",
      "Open original on Instagram",
    );
    expect(instagramLink).not.toHaveTextContent("Open original on Instagram");
    expect(instagramLink.querySelector("svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );

    const deleteButton = within(actions).getByRole("button", {
      name: "Delete downloaded media",
    });
    expect(deleteButton).not.toHaveTextContent("Delete downloaded media");
    expect(deleteButton).toHaveAttribute(
      "data-tooltip",
      "Delete downloaded media",
    );
    expect(deleteButton.querySelector("svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });

  it("keeps delete confirmation before media removal", async () => {
    const user = userEvent.setup();
    renderViewer(reelFixture);

    await screen.findByRole("heading", { name: "Reel" });
    const actions = screen.getByRole("group", { name: "Media actions" });
    const deleteButton = within(actions).getByRole("button", {
      name: "Delete downloaded media",
    });
    await user.click(deleteButton);

    expect(
      screen.getByRole("dialog", { name: "Delete this media item?" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Delete media" }),
    ).toBeVisible();
  });
});
