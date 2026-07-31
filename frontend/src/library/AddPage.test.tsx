import { HttpResponse, http } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";
import { classifyAddInput } from "./instagramInput";

const authenticatedSession = {
  username: "owner",
  must_change_password: false,
  expires_at: "2026-08-02T00:00:00Z",
  csrf_token: "c".repeat(64),
};

const jobFixture = {
  id: "job-1",
  type: "download_media",
  state: "pending",
  payload: {},
  progress_current: 0,
  progress_total: null,
  status_text: "Media download queued.",
  error: null,
  created_at: "2026-08-01T00:00:00Z",
  started_at: null,
  completed_at: null,
  updated_at: "2026-08-01T00:00:00Z",
};

const profileCreateFixture = {
  profile: {
    id: "profile-1",
    instagram_user_id: "123",
    username: "natgeo",
    full_name: "National Geographic",
    biography: "Stories from around the world.",
    profile_pic_url: null,
    tracked: true,
    status: "active",
    last_sync_attempted_at: null,
    last_sync_succeeded_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    media_count: 0,
  },
  job: jobFixture,
};

function successEnvelope<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

describe("AddPage", () => {
  it.each([
    [
      "credential-bearing URL",
      "https://account:secret@www.instagram.com/p/ABCDE12345/",
    ],
    [
      "non-default port URL",
      "https://www.instagram.com:8443/p/ABCDE12345/",
    ],
    [
      "Story URL with an invalid username",
      "https://www.instagram.com/stories/katerina-soria/3952742051065980676",
    ],
    [
      "Story URL with a story ID longer than 32 digits",
      "https://www.instagram.com/stories/katerina.soria/123456789012345678901234567890123",
    ],
  ])("keeps a %s profile-routed", (_description, input) => {
    expect(classifyAddInput(input)).toBe("profile");
  });

  it("queues a query-bearing Story URL as media", async () => {
    let mediaInput = "";
    let profileCalls = 0;
    server.use(
      http.post("/api/media", async ({ request }) => {
        mediaInput = ((await request.json()) as { input: string }).input;
        return HttpResponse.json(successEnvelope(jobFixture));
      }),
      http.post("/api/profiles", () => {
        profileCalls += 1;
        return HttpResponse.json(successEnvelope(profileCreateFixture));
      }),
    );
    render(
      <TestRouter initialPath="/add" initialSession={authenticatedSession} />,
    );

    const user = userEvent.setup();
    await user.type(
      screen.getByLabelText("Instagram link or profile"),
      "https://www.instagram.com/stories/katerina.soria/3952742051065980676"
        + "?utm_source=ig_story_item_share&igsh=secret",
    );
    await user.click(screen.getByRole("button", { name: "Add to library" }));

    expect(
      await screen.findByRole("heading", { name: "Download queued" }),
    ).toBeVisible();
    expect(mediaInput).toContain("/stories/");
    expect(profileCalls).toBe(0);
  });

  it.each([
    ["a profile", "https://www.instagram.com/natgeo/", "/api/profiles"],
    ["a post", "https://www.instagram.com/p/ABCDE12345/", "/api/media"],
    ["a Reel", "https://www.instagram.com/reel/ABCDE12345/", "/api/media"],
    ["a TV post", "https://www.instagram.com/tv/ABCDE12345/", "/api/media"],
  ])("routes %s to %s", async (_kind, input, expectedEndpoint) => {
    const requests: string[] = [];
    server.use(
      http.post("/api/media", () => {
        requests.push("/api/media");
        return HttpResponse.json(successEnvelope(jobFixture));
      }),
      http.post("/api/profiles", () => {
        requests.push("/api/profiles");
        return HttpResponse.json(successEnvelope(profileCreateFixture));
      }),
    );
    render(
      <TestRouter initialPath="/add" initialSession={authenticatedSession} />,
    );

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Instagram link or profile"), input);
    await user.click(screen.getByRole("button", { name: "Add to library" }));

    expect(
      await screen.findByRole("heading", { name: "Download queued" }),
    ).toBeVisible();
    expect(requests).toEqual([expectedEndpoint]);
  });
});
