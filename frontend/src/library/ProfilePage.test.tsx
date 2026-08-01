import { HttpResponse, http } from "msw";
import { render, screen, waitFor } from "@testing-library/react";
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

function successEnvelope<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

function renderProfile(mediaQueries: string[] = []) {
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
      initialPath="/profiles/profile-1"
      initialSession={authenticatedSession}
    />,
  );
  return mediaQueries;
}

describe("ProfilePage", () => {
  it("shows all media tabs, links safely to the profile, and requests Stories", async () => {
    const mediaQueries = renderProfile();
    const postsTab = await screen.findByRole("tab", { name: "Posts" });
    expect(postsTab).toBeVisible();
    expect(screen.getByRole("tab", { name: "Reels" })).toBeVisible();
    const storyTab = screen.getByRole("tab", { name: "Story" });
    expect(storyTab).toBeVisible();

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
      expect(mediaQueries.at(-1)).toContain("kind=story");
    });
  });

  it("connects the tabs to their panel and keeps only the selected tab in the tab order", async () => {
    renderProfile();

    const postsTab = await screen.findByRole("tab", { name: "Posts" });
    const reelsTab = screen.getByRole("tab", { name: "Reels" });
    const storyTab = screen.getByRole("tab", { name: "Story" });
    const panel = screen.getByRole("tabpanel");

    expect(panel).toHaveAttribute("id", "profile-media-panel");
    expect(panel).toHaveAttribute("aria-labelledby", "posts-tab");
    expect(postsTab).toHaveAttribute("aria-controls", "profile-media-panel");
    expect(reelsTab).toHaveAttribute("aria-controls", "profile-media-panel");
    expect(storyTab).toHaveAttribute("aria-controls", "profile-media-panel");
    expect(postsTab).toHaveAttribute("tabindex", "0");
    expect(reelsTab).toHaveAttribute("tabindex", "-1");
    expect(storyTab).toHaveAttribute("tabindex", "-1");
  });

  it("moves focus and selection with Arrow, Home, and End keys", async () => {
    const mediaQueries = renderProfile();
    const user = userEvent.setup();
    const postsTab = await screen.findByRole("tab", { name: "Posts" });
    const reelsTab = screen.getByRole("tab", { name: "Reels" });
    const storyTab = screen.getByRole("tab", { name: "Story" });

    postsTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(reelsTab).toHaveFocus();
    expect(reelsTab).toHaveAttribute("aria-selected", "true");
    expect(reelsTab).toHaveAttribute("tabindex", "0");
    expect(postsTab).toHaveAttribute("tabindex", "-1");

    await user.keyboard("{ArrowLeft}");
    expect(postsTab).toHaveFocus();
    expect(postsTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowLeft}");
    expect(storyTab).toHaveFocus();
    expect(storyTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Home}");
    expect(postsTab).toHaveFocus();
    expect(postsTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{End}");
    expect(storyTab).toHaveFocus();
    expect(storyTab).toHaveAttribute("aria-selected", "true");
    expect(storyTab).toHaveAttribute("tabindex", "0");
    expect(reelsTab).toHaveAttribute("tabindex", "-1");

    await waitFor(() => {
      expect(mediaQueries.at(-1)).toContain("kind=story");
    });
  });
});
