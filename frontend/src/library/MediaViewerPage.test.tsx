import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useNavigate } from "react-router-dom";

import { AppRoutes } from "../app/App";
import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";

const authenticatedSession = {
  username: "owner",
  must_change_password: false,
  expires_at: "2026-08-02T00:00:00Z",
  csrf_token: "c".repeat(64),
};
const RENDERED_SLIDE_LIMIT = 5;

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

const newerReelFixture = {
  ...reelFixture,
  id: "reel-newer",
  shortcode: "NEWER",
  identity_value: "NEWER",
  owner_profile_id: "profile-2",
  original_url: "https://www.instagram.com/reel/NEWER/",
  published_at: "2026-08-02T00:00:00Z",
  assets: [],
};

const olderReelFixture = {
  ...reelFixture,
  id: "reel-older",
  shortcode: "OLDER",
  identity_value: "OLDER",
  owner_profile_id: "profile-3",
  original_url: "https://www.instagram.com/reel/OLDER/",
  published_at: "2026-07-31T00:00:00Z",
  assets: [],
};

const secondProfileFixture = {
  ...profileFixture,
  id: "profile-2",
  username: "newer.owner",
};

const thirdProfileFixture = {
  ...profileFixture,
  id: "profile-3",
  username: "older.owner",
};

function successEnvelope<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

function renderViewer(
  media: typeof reelFixture | typeof storyFixture | typeof carouselFixture,
  options: Readonly<{
    items?: readonly (
      | typeof reelFixture
      | typeof storyFixture
      | typeof carouselFixture
      | typeof newerReelFixture
      | typeof olderReelFixture
    )[];
    source?: "profile" | "recent";
    newerCursor?: string | null;
    olderCursor?: string | null;
    onCursorRequest?: (cursor: string) => void;
    cursorItems?: Readonly<Record<string, readonly (
      | typeof reelFixture
      | typeof storyFixture
      | typeof carouselFixture
      | typeof newerReelFixture
      | typeof olderReelFixture
    )[]>>;
    cursorFailures?: Readonly<Record<string, number>>;
    cursorDelayMs?: Readonly<Record<string, number>>;
    cursorContinuations?: Readonly<Record<string, Readonly<{
      newerCursor?: string | null;
      olderCursor?: string | null;
    }>>>;
    anchorItems?: Readonly<Record<string, readonly (
      | typeof reelFixture
      | typeof storyFixture
      | typeof carouselFixture
      | typeof newerReelFixture
      | typeof olderReelFixture
    )[]>>;
    profileDelayMs?: Readonly<Record<string, number>>;
    extraChildren?: ReactNode;
  }> = {},
) {
  const source = options.source ?? "profile";
  const items = options.items ?? [media];
  const remainingCursorFailures = { ...options.cursorFailures };
  server.use(
    http.get("/api/media/feed", async ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      if (cursor !== null) {
        options.onCursorRequest?.(cursor);
        const delayMs = options.cursorDelayMs?.[cursor] ?? 0;
        if (delayMs > 0) {
          await new Promise((resolve) => window.setTimeout(resolve, delayMs));
        }
        if ((remainingCursorFailures[cursor] ?? 0) > 0) {
          remainingCursorFailures[cursor] -= 1;
          return HttpResponse.json(
            {
              success: false,
              data: null,
              error: {
                code: "page_load_failed",
                message: `${cursor} failed`,
              },
              meta: {},
            },
            { status: 503 },
          );
        }
        const continuation = options.cursorContinuations?.[cursor];
        return HttpResponse.json(
          successEnvelope({
            items: options.cursorItems?.[cursor] ?? [storyFixture],
            newer_cursor: continuation?.newerCursor ?? null,
            older_cursor: continuation?.olderCursor ?? null,
          }),
        );
      }
      const anchorId = new URL(request.url).searchParams.get("anchor_id");
      return HttpResponse.json(
        successEnvelope({
          items: anchorId === null
            ? items
            : options.anchorItems?.[anchorId] ?? items,
          newer_cursor: options.newerCursor ?? null,
          older_cursor: options.olderCursor ?? null,
        }),
      );
    }),
    http.get(`/api/media/${media.id}`, () =>
      HttpResponse.json(successEnvelope(media)),
    ),
    http.get("/api/profiles/:profileId", async ({ params }) => {
      const profileId = String(params.profileId);
      const delayMs = options.profileDelayMs?.[profileId] ?? 0;
      if (delayMs > 0) {
        await new Promise((resolve) => window.setTimeout(resolve, delayMs));
      }
      const profile = profileId === "profile-2"
        ? secondProfileFixture
        : profileId === "profile-3"
          ? thirdProfileFixture
          : profileFixture;
      return HttpResponse.json(successEnvelope(profile));
    }),
    http.get("/api/profiles", () =>
      HttpResponse.json(successEnvelope([profileFixture])),
    ),
    http.get("/api/media", () => HttpResponse.json(successEnvelope([]))),
  );
  const sourceQuery = source === "recent"
    ? "source=recent"
    : `source=profile&profileId=profile-1&kind=${media.kind}`;
  return render(
    <TestRouter
      initialPath={`/media/${media.id}?${sourceQuery}`}
      initialSession={authenticatedSession}
    >
      <AppRoutes />
      {options.extraChildren}
    </TestRouter>,
  );
}

function ViewerRouteControl() {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() => navigate("/media/story-1?source=recent")}
    >
      Open story viewer
    </button>
  );
}

describe("MediaViewerPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.spyOn(HTMLMediaElement.prototype, "pause")
      .mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, "play")
      .mockResolvedValue(undefined);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

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

  it("renders a vertical feed and moves one media item with ArrowDown", async () => {
    renderViewer(reelFixture, {
      items: [reelFixture, storyFixture],
      source: "recent",
    });

    const feed = await screen.findByRole("region", { name: "Media feed" });
    const reelSlide = within(feed).getByRole("group", {
      name: "Reel REEL123",
    });
    const storySlide = within(feed).getByRole("group", {
      name: "Story 3952742051065980676",
    });
    expect(reelSlide).toHaveAttribute("aria-current", "true");

    fireEvent.keyDown(window, { key: "ArrowDown" });

    expect(storySlide).toHaveAttribute("aria-current", "true");
    expect(reelSlide).not.toHaveAttribute("aria-current", "true");
  });

  it("autoplays only the active video and persists the muted choice", async () => {
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    const view = renderViewer(reelFixture, { source: "recent" });

    await screen.findByRole("region", { name: "Media feed" });
    const video = view.container.querySelector("video");
    expect(video).not.toBeNull();
    expect(video?.autoplay).toBe(true);
    expect(video?.muted).toBe(true);
    expect(play).toHaveBeenCalled();

    if (video !== null) {
      video.muted = false;
      fireEvent.volumeChange(video);
    }
    expect(window.localStorage.getItem("instaloader-webui:media-muted")).toBe(
      "false",
    );

    view.unmount();
    const secondView = renderViewer(reelFixture, { source: "recent" });
    await screen.findByRole("region", { name: "Media feed" });
    expect(secondView.container.querySelector("video")?.muted).toBe(false);
  });

  it("returns Recent media to Home with its link and Escape", async () => {
    renderViewer(reelFixture, { source: "recent" });

    expect(
      await screen.findByRole("link", { name: "Back to recent media" }),
    ).toHaveAttribute("href", "/");

    fireEvent.keyDown(window, { key: "Escape" });

    expect(
      await screen.findByRole("heading", { name: "Welcome back, owner" }),
    ).toBeVisible();
  });

  it("returns profile media to the same profile tab", async () => {
    renderViewer(storyFixture);

    expect(
      await screen.findByRole("link", { name: "Back to profile" }),
    ).toHaveAttribute("href", "/profiles/profile-1?tab=story");
  });

  it("loads the next cursor page before reaching the loaded boundary", async () => {
    let requestedCursor = "";
    renderViewer(reelFixture, {
      olderCursor: "older-page-1",
      source: "recent",
      onCursorRequest: (cursor) => {
        requestedCursor = cursor;
      },
    });

    const feed = await screen.findByRole("region", { name: "Media feed" });
    expect(
      await within(feed).findByRole("group", {
        name: "Story 3952742051065980676",
      }),
    ).toBeVisible();
    expect(requestedCursor).toBe("older-page-1");
  });

  it("keeps the next control available and advances after the boundary page loads", async () => {
    const user = userEvent.setup();
    renderViewer(reelFixture, {
      source: "recent",
      olderCursor: "older-page-1",
      cursorItems: { "older-page-1": [olderReelFixture] },
      cursorDelayMs: { "older-page-1": 60 },
    });

    await screen.findByRole("region", { name: "Media feed" });
    await user.click(screen.getByRole("button", { name: "Next media" }));

    expect(
      await screen.findByRole("group", { name: "Reel OLDER", current: true }),
    ).toBeInTheDocument();
  });

  it("advances upward after prepending a boundary page without restoring the old position", async () => {
    const user = userEvent.setup();
    renderViewer(reelFixture, {
      source: "recent",
      newerCursor: "newer-page-1",
      cursorItems: { "newer-page-1": [newerReelFixture] },
      cursorDelayMs: { "newer-page-1": 60 },
    });

    const feed = await screen.findByRole("region", { name: "Media feed" });
    Object.defineProperty(feed, "clientHeight", {
      configurable: true,
      value: 640,
    });
    feed.scrollTo = ((optionsOrX?: ScrollToOptions | number) => {
      const top = typeof optionsOrX === "number"
        ? 0
        : optionsOrX?.top ?? 0;
      Object.defineProperty(feed, "scrollTop", {
        configurable: true,
        value: Number(top),
      });
    }) as typeof feed.scrollTo;

    await user.click(screen.getByRole("button", { name: "Previous media" }));

    expect(
      await screen.findByRole("group", { name: "Reel NEWER", current: true }),
    ).toBeInTheDocument();
    await waitFor(() => expect(feed.scrollTop).toBe(0));
  });

  it("resets the scroll anchor when another media opens in the same route instance", async () => {
    const user = userEvent.setup();
    renderViewer(reelFixture, {
      source: "recent",
      items: [reelFixture, storyFixture],
      anchorItems: {
        "reel-1": [reelFixture, storyFixture],
        "story-1": [reelFixture, storyFixture],
      },
      extraChildren: <ViewerRouteControl />,
    });

    const feed = await screen.findByRole("region", { name: "Media feed" });
    Object.defineProperty(feed, "clientHeight", {
      configurable: true,
      value: 640,
    });
    Object.defineProperty(feed, "scrollTop", {
      configurable: true,
      writable: true,
      value: 0,
    });

    await user.click(screen.getByRole("button", { name: "Open story viewer" }));

    expect(
      await screen.findByRole("group", {
        name: "Story 3952742051065980676",
        current: true,
      }),
    ).toBeInTheDocument();
    await waitFor(() => expect(feed.scrollTop).toBe(640));
  });

  it("jumps directly to a middle anchor with smooth scrolling disabled", async () => {
    const user = userEvent.setup();
    const originalClientHeight = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      "clientHeight",
    );
    const originalScrollTo = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      "scrollTo",
    );
    const jumps: Array<Readonly<{
      behavior: ScrollBehavior | undefined;
      inlineBehavior: string;
      top: number;
    }>> = [];
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
      configurable: true,
      get() {
        return this.classList.contains("viewer-feed-track") ? 640 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value(this: HTMLElement, options: ScrollToOptions) {
        if (this.classList.contains("viewer-feed-track")) {
          jumps.push({
            behavior: options.behavior,
            inlineBehavior: this.style.scrollBehavior,
            top: Number(options.top ?? 0),
          });
        }
      },
    });

    try {
      renderViewer(storyFixture, {
        source: "recent",
        items: [reelFixture, storyFixture],
        anchorItems: {
          "story-1": [reelFixture, storyFixture],
        },
      });

      expect(
        await screen.findByRole("group", {
          name: "Story 3952742051065980676",
          current: true,
        }),
      ).toBeInTheDocument();
      await waitFor(() => expect(jumps).toEqual([{
        behavior: "auto",
        inlineBehavior: "auto",
        top: 640,
      }]));
      await user.click(
        screen.getByRole("button", { name: "Previous media" }),
      );
      expect(jumps[1]).toEqual({
        behavior: "smooth",
        inlineBehavior: "",
        top: 0,
      });
    } finally {
      if (originalClientHeight === undefined) {
        Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
      } else {
        Object.defineProperty(
          HTMLElement.prototype,
          "clientHeight",
          originalClientHeight,
        );
      }
      if (originalScrollTo === undefined) {
        Reflect.deleteProperty(HTMLElement.prototype, "scrollTo");
      } else {
        Object.defineProperty(
          HTMLElement.prototype,
          "scrollTo",
          originalScrollTo,
        );
      }
    }
  });

  it("keeps both pages when newer and older cursor loads finish out of order", async () => {
    renderViewer(reelFixture, {
      source: "recent",
      newerCursor: "newer-page",
      olderCursor: "older-page",
      cursorItems: {
        "newer-page": [newerReelFixture],
        "older-page": [olderReelFixture],
      },
      profileDelayMs: { "profile-2": 40 },
    });

    const feed = await screen.findByRole("region", { name: "Media feed" });
    expect(
      await within(feed).findByRole("group", { name: "Reel NEWER" }),
    ).toBeInTheDocument();
    expect(
      within(feed).getByRole("group", { name: "Reel OLDER" }),
    ).toBeInTheDocument();
  });

  it("keeps only a bounded window of media slides in the DOM", async () => {
    const items = [
      reelFixture,
      ...Array.from({ length: 11 }, (_, index) => ({
        ...reelFixture,
        id: `windowed-reel-${index}`,
        shortcode: `WINDOW${index}`,
        identity_value: `WINDOW${index}`,
        assets: [],
      })),
    ];
    renderViewer(reelFixture, { items, source: "recent" });

    const feed = await screen.findByRole("region", { name: "Media feed" });

    expect(feed.querySelectorAll(".viewer-feed-slide").length).toBeLessThanOrEqual(
      RENDERED_SLIDE_LIMIT,
    );
    expect(feed.querySelector(".viewer-feed-spacer")).toBeInTheDocument();
  });

  it("retries the exact cursor direction that failed", async () => {
    const cursorRequests: string[] = [];
    renderViewer(reelFixture, {
      source: "recent",
      items: [
        storyFixture,
        carouselFixture,
        reelFixture,
        newerReelFixture,
        olderReelFixture,
        storyFixture,
      ],
      newerCursor: "newer-page",
      olderCursor: "older-page",
      cursorItems: {
        "newer-page": [newerReelFixture],
      },
      cursorFailures: { "newer-page": 1 },
      cursorDelayMs: { "newer-page": 20 },
      onCursorRequest: (cursor) => cursorRequests.push(cursor),
    });
    const user = userEvent.setup();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "newer-page failed",
    );
    await user.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => {
      expect(cursorRequests.filter((cursor) => cursor === "newer-page"))
        .toHaveLength(2);
    });
    expect(cursorRequests).not.toContain("older-page");
  });
});
