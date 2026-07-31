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

describe("ProfilePage", () => {
  it("shows all media tabs, links safely to the profile, and requests Stories", async () => {
    const mediaQueries: string[] = [];
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

    await userEvent.click(storyTab);

    await waitFor(() => {
      expect(mediaQueries.at(-1)).toContain("kind=story");
    });
  });
});
