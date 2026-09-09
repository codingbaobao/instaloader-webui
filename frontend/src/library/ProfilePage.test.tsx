import { HttpResponse, http } from "msw";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useLocation, useNavigate } from "react-router-dom";

import { AppRoutes } from "../app/App";
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

function successEnvelope<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

function renderProfile(
  mediaQueries: string[] = [],
  initialPath = "/profiles/profile-1",
) {
  server.use(
    http.get("/api/profiles/profile-1", () =>
      HttpResponse.json(successEnvelope(profileFixture)),
    ),
    http.get("/api/media", ({ request }) => {
      mediaQueries.push(new URL(request.url).search);
      return HttpResponse.json(successEnvelope([]));
    }),
  );
  render(
    <TestRouter
      initialPath={initialPath}
      initialSession={authenticatedSession}
    />,
  );
  return mediaQueries;
}

function HistoryProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="location-search">{location.search}</output>
      <button type="button" onClick={() => navigate(-1)}>Browser back</button>
    </>
  );
}

describe("ProfilePage", () => {
  it("shows only Feed and Story tabs and requests each collection", async () => {
    const mediaQueries = renderProfile();
    const feedTab = await screen.findByRole("tab", { name: "Feed" });
    expect(feedTab).toBeVisible();
    const storyTab = screen.getByRole("tab", { name: "Story" });
    expect(storyTab).toBeVisible();
    expect(screen.queryByRole("tab", { name: "Posts" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Reels" })).not.toBeInTheDocument();

    await waitFor(() => {
      expect(mediaQueries.at(-1)).toContain("collection=feed");
    });
    expect(mediaQueries.at(-1)).not.toContain("kind=");

    const instagramLink = screen.getByRole("link", {
      name: "Open @katerina.soria on Instagram",
    });
    expect(instagramLink).toHaveAttribute(
      "href",
      "https://www.instagram.com/katerina.soria/",
    );
    expect(instagramLink).toHaveAttribute("target", "_blank");
    expect(instagramLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(instagramLink).toHaveAttribute(
      "data-tooltip",
      "Open @katerina.soria on Instagram",
    );
    expect(instagramLink).not.toHaveTextContent("Instagram");
    expect(instagramLink.querySelector("svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(instagramLink.querySelector("svg")).toHaveAttribute(
      "focusable",
      "false",
    );

    await userEvent.click(storyTab);

    await waitFor(() => {
      expect(mediaQueries.at(-1)).toContain("collection=story");
    });
    expect(mediaQueries.at(-1)).not.toContain("kind=");
  });

  it("connects the tabs to their panel and keeps only the selected tab in the tab order", async () => {
    renderProfile();

    const feedTab = await screen.findByRole("tab", { name: "Feed" });
    const storyTab = screen.getByRole("tab", { name: "Story" });
    const panel = screen.getByRole("tabpanel");

    expect(panel).toHaveAttribute("id", "profile-media-panel");
    expect(panel).toHaveAttribute("aria-labelledby", "feed-tab");
    expect(feedTab).toHaveAttribute("aria-controls", "profile-media-panel");
    expect(storyTab).toHaveAttribute("aria-controls", "profile-media-panel");
    expect(feedTab).toHaveAttribute("tabindex", "0");
    expect(storyTab).toHaveAttribute("tabindex", "-1");
  });

  it("moves focus and selection with Arrow, Home, and End keys", async () => {
    const mediaQueries = renderProfile();
    const user = userEvent.setup();
    const feedTab = await screen.findByRole("tab", { name: "Feed" });
    const storyTab = screen.getByRole("tab", { name: "Story" });

    feedTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(storyTab).toHaveFocus();
    expect(storyTab).toHaveAttribute("aria-selected", "true");
    expect(storyTab).toHaveAttribute("tabindex", "0");
    expect(feedTab).toHaveAttribute("tabindex", "-1");

    await user.keyboard("{ArrowLeft}");
    expect(feedTab).toHaveFocus();
    expect(feedTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowLeft}");
    expect(storyTab).toHaveFocus();
    expect(storyTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Home}");
    expect(feedTab).toHaveFocus();
    expect(feedTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{End}");
    expect(storyTab).toHaveFocus();
    expect(storyTab).toHaveAttribute("aria-selected", "true");
    expect(storyTab).toHaveAttribute("tabindex", "0");
    expect(feedTab).toHaveAttribute("tabindex", "-1");

    await waitFor(() => {
      expect(mediaQueries.at(-1)).toContain("collection=story");
    });
  });

  it("defaults an unknown profile tab to Feed without requesting Reel", async () => {
    const mediaQueries = renderProfile([], "/profiles/profile-1?tab=reel");

    const feedTab = await screen.findByRole("tab", { name: "Feed" });
    expect(feedTab).toHaveAttribute("aria-selected", "true");
    expect(feedTab).toHaveAttribute("tabindex", "0");
    await waitFor(() => {
      expect(mediaQueries.at(-1)).toContain("collection=feed");
    });
    expect(mediaQueries.at(-1)).not.toContain("collection=reel");
    expect(mediaQueries.at(-1)).not.toContain("kind=");
  });

  it("pushes tab changes into history and restores the tab on browser back", async () => {
    server.use(
      http.get("/api/profiles/profile-1", () =>
        HttpResponse.json(successEnvelope(profileFixture)),
      ),
      http.get("/api/media", () => HttpResponse.json(successEnvelope([]))),
    );
    render(
      <TestRouter
        initialPath="/profiles/profile-1?tab=feed"
        initialSession={authenticatedSession}
      >
        <AppRoutes />
        <HistoryProbe />
      </TestRouter>,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("tab", { name: "Story" }));
    expect(screen.getByTestId("location-search")).toHaveTextContent("tab=story");
    expect(screen.getByRole("tab", { name: "Story" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "Browser back" }));

    await waitFor(() => {
      expect(screen.getByTestId("location-search")).toHaveTextContent("tab=feed");
      expect(screen.getByRole("tab", { name: "Feed" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
  });
});
