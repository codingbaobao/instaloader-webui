import { HttpResponse, http } from "msw";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const carouselFixture = {
  ...reelFixture,
  id: "post-1",
  instagram_media_id: "789",
  shortcode: "POST123",
  identity_value: "POST123",
  kind: "post",
  original_url: "https://www.instagram.com/p/POST123/",
  assets: [
    {
      ...reelFixture.assets[1],
      id: "content-image-1",
      media_id: "post-1",
      relative_path: "image-1.jpg",
      role: "content",
      position: 0,
    },
    {
      ...reelFixture.assets[0],
      id: "content-video-2",
      media_id: "post-1",
      relative_path: "video-2.mp4",
      position: 1,
    },
    {
      ...reelFixture.assets[1],
      id: "poster-2",
      media_id: "post-1",
      relative_path: "video-2.jpg",
      position: 1,
    },
    {
      ...reelFixture.assets[1],
      id: "content-image-3",
      media_id: "post-1",
      relative_path: "image-3.jpg",
      role: "content",
      position: 2,
    },
  ],
};

function successEnvelope<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

function renderViewer(
  media: typeof reelFixture | typeof storyFixture | typeof carouselFixture,
) {
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
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, "pause")
      .mockImplementation(() => undefined);
  });
  afterEach(() => vi.restoreAllMocks());

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

  it("uses arrow-only controls and stops at the carousel boundaries", async () => {
    const user = userEvent.setup();
    const pause = vi.mocked(HTMLMediaElement.prototype.pause);
    const { container } = renderViewer(carouselFixture);

    const carousel = await screen.findByRole("region", {
      name: "Post media carousel",
    });
    Object.defineProperty(carousel, "clientWidth", {
      configurable: true,
      value: 320,
    });
    carousel.scrollTo = ((optionsOrX?: ScrollToOptions | number) => {
      const left = typeof optionsOrX === "number"
        ? optionsOrX
        : optionsOrX?.left ?? 0;
      Object.defineProperty(carousel, "scrollLeft", {
        configurable: true,
        value: Number(left),
      });
      fireEvent.scroll(carousel);
    }) as typeof carousel.scrollTo;

    expect(within(carousel).getAllByRole("group")).toHaveLength(3);
    const video = container.querySelector("video");
    expect(video).toHaveAttribute(
      "src",
      "/api/media/post-1/assets/content-video-2",
    );
    expect(video).toHaveAttribute(
      "poster",
      "/api/media/post-1/assets/poster-2",
    );
    expect(
      screen.queryByRole("button", { name: "Previous image or video" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Next image or video" }),
    ).not.toHaveTextContent("Next");
    expect(screen.getByText("1 / 3")).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "Next image or video" }),
    );
    expect(screen.getByText("2 / 3")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Previous image or video" }),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "Next image or video" }),
    );
    expect(screen.getByText("3 / 3")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Next image or video" }),
    ).not.toBeInTheDocument();
    expect(pause).toHaveBeenCalledTimes(1);
  });

  it("synchronizes the controls after native horizontal scrolling", async () => {
    renderViewer(carouselFixture);

    const carousel = await screen.findByRole("region", {
      name: "Post media carousel",
    });
    Object.defineProperties(carousel, {
      clientWidth: { configurable: true, value: 320 },
      scrollLeft: { configurable: true, value: 640 },
    });
    fireEvent.scroll(carousel);

    expect(screen.getByText("3 / 3")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Previous image or video" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Next image or video" }),
    ).not.toBeInTheDocument();
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
