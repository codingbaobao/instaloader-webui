import { HttpResponse, http } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";
import type { JobSummary } from "./types";

const authenticatedSession = {
  username: "owner",
  must_change_password: false,
  expires_at: "2026-08-02T00:00:00Z",
  csrf_token: "c".repeat(64),
};

const baseJob: JobSummary = {
  id: "job-1",
  type: "profile_sync",
  state: "running",
  payload: {},
  progress_current: 0,
  progress_total: null,
  status_text: "Syncing profile.",
  error: null,
  phase: null,
  target_label: null,
  target_url: null,
  progress_segments: [],
  issue_count: 0,
  issues: [],
  created_at: "2026-07-31T08:00:00Z",
  started_at: "2026-07-31T08:00:01Z",
  completed_at: null,
  updated_at: "2026-07-31T08:00:02Z",
};

function jobFixture(overrides: Partial<JobSummary> = {}): JobSummary {
  return { ...baseJob, ...overrides };
}

function successEnvelope<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

describe("ActivityPage", () => {
  it("shows a profile target and ordered Stories and Feed content progress", async () => {
    server.use(
      http.get("/api/jobs", () =>
        HttpResponse.json(successEnvelope([
          jobFixture({
            payload: { profile_id: "profile-1" },
            target_label: "@mihi_727",
            progress_segments: [
              {
                segment: "stories",
                label: "Stories",
                state: "running",
                scanned: 3,
                total: null,
                saved: 1,
                existing: 2,
                warnings: 0,
                updated_at: "2026-07-31T08:00:03Z",
              },
              {
                segment: "feed",
                label: "Feed content",
                state: "running",
                scanned: 5,
                total: 10,
                saved: 2,
                existing: 2,
                warnings: 1,
                updated_at: "2026-07-31T08:00:04Z",
              },
            ],
          }),
        ])),
      ),
    );

    render(
      <TestRouter initialPath="/activity" initialSession={authenticatedSession} />,
    );

    const profileLink = await screen.findByRole("link", { name: "@mihi_727" });
    expect(profileLink).toHaveAttribute("href", "/profiles/profile-1");
    expect(screen.getByRole("heading", { name: /profile sync @mihi_727/i })).toBeVisible();
    const progressbars = screen.getAllByRole("progressbar");
    expect(progressbars).toHaveLength(2);
    expect(progressbars[0]).toHaveAccessibleName("@mihi_727 Stories progress");
    expect(progressbars[0]).not.toHaveAttribute("value");
    expect(progressbars[1]).toHaveAccessibleName("@mihi_727 Feed content progress");
    expect(progressbars[1]).toHaveAttribute("value", "50");
    expect(screen.getByText("Scanned 3")).toBeVisible();
    expect(screen.getByText("Saved 1")).toBeVisible();
    expect(screen.getAllByText("Existing 2")).toHaveLength(2);
    expect(screen.getByText("Warnings 1")).toBeVisible();
    expect(screen.getAllByText(/Stories|Feed content/).map((node) => node.textContent)).toEqual(
      expect.arrayContaining(["Stories", "Feed content"]),
    );
  });

  it("shows a canonical single-media target as a safe external link", async () => {
    const canonicalUrl = "https://www.instagram.com/reel/DOqEJyxCRGJ/";
    server.use(
      http.get("/api/jobs", () =>
        HttpResponse.json(successEnvelope([
          jobFixture({
            type: "single_media",
            target_label: canonicalUrl,
            target_url: canonicalUrl,
          }),
        ])),
      ),
    );

    render(
      <TestRouter initialPath="/activity" initialSession={authenticatedSession} />,
    );

    const target = await screen.findByRole("link", { name: canonicalUrl });
    expect(target).toHaveAttribute("href", canonicalUrl);
    expect(target).toHaveAttribute("target", "_blank");
    expect(target).toHaveAttribute("rel", "noreferrer");
  });

  it("shows a scanning phase without a false count, percentage, or progress bar", async () => {
    server.use(
      http.get("/api/jobs", () =>
        HttpResponse.json(successEnvelope([
          jobFixture({
            phase: "scanning_media",
            progress_current: 2,
            progress_total: null,
            status_text: "Scanning Instagram posts and reels…",
          }),
        ])),
      ),
    );

    render(
      <TestRouter
        initialPath="/activity"
        initialSession={authenticatedSession}
      />,
    );

    expect(
      await screen.findByText("Scanning Instagram posts and reels…"),
    ).toBeVisible();
    expect(screen.queryByText("Progress pending")).not.toBeInTheDocument();
    expect(screen.queryByText(/2 of/)).not.toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("omits numeric progress when a delete job has a zero total", async () => {
    server.use(
      http.get("/api/jobs", () =>
        HttpResponse.json(successEnvelope([
          jobFixture({
            type: "delete_media",
            state: "succeeded",
            progress_current: 0,
            progress_total: 0,
            status_text: "Media deleted.",
            completed_at: "2026-07-31T08:00:04Z",
          }),
        ])),
      ),
    );

    render(
      <TestRouter
        initialPath="/activity"
        initialSession={authenticatedSession}
      />,
    );

    expect(await screen.findByText("Media deleted.")).toBeVisible();
    expect(screen.queryByText("0 of 0")).not.toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("shows the sanitized failure reason for an ordinary failed job", async () => {
    server.use(
      http.get("/api/jobs", () =>
        HttpResponse.json(successEnvelope([
          jobFixture({
            state: "failed",
            status_text: "Profile sync failed.",
            error: "Filesystem operation failed: Permission denied.",
            completed_at: "2026-07-31T08:00:04Z",
          }),
        ])),
      ),
    );

    render(
      <TestRouter
        initialPath="/activity"
        initialSession={authenticatedSession}
      />,
    );

    expect(
      await screen.findByRole("alert"),
    ).toHaveTextContent("Filesystem operation failed: Permission denied.");
    expect(screen.getByText("Failed")).toBeVisible();
  });

  it("loads and shows only safe warning details after expansion", async () => {
    const warningJob = jobFixture({
      state: "completed_with_warnings",
      payload: {
        input: "https://www.instagram.com/reel/DOqEJyxCRGJ/?sessionid=secret",
      },
      progress_current: 2,
      progress_total: 2,
      status_text: "Downloaded 1 of 2 media items.",
      error: "Traceback: BadResponseException for /?__a=1",
      phase: "completed",
      issue_count: 1,
      completed_at: "2026-07-31T08:00:04Z",
    });
    const warningDetail = {
      ...warningJob,
      issues: [
        {
          identity_type: "shortcode",
          identity_value: "DOqEJyxCRGJ",
          shortcode: "DOqEJyxCRGJ",
          story_media_id: null,
          media_kind: "reel",
          error_code: "instagram_unavailable",
          safe_message: "Instagram could not be reached. Try again later.",
          exception_class_chain: [
            "BadResponseException",
            "ConnectionException",
          ],
          occurred_at: "2026-07-31T08:00:03Z",
          request_url: "https://www.instagram.com/reel/DOqEJyxCRGJ/?__a=1",
          raw_exception: "Traceback: sessionid=secret",
          raw_html: "<script>stealCookies()</script>",
        },
      ],
    };
    let detailRequests = 0;
    server.use(
      http.get("/api/jobs", () =>
        HttpResponse.json(successEnvelope([warningJob])),
      ),
      http.get("/api/jobs/job-1", () => {
        detailRequests += 1;
        return HttpResponse.json(successEnvelope(warningDetail));
      }),
    );

    render(
      <TestRouter
        initialPath="/activity"
        initialSession={authenticatedSession}
      />,
    );

    expect(await screen.findByText("Completed with warnings")).toBeVisible();
    expect(screen.getByRole("button", { name: "View 1 warning" })).toBeVisible();
    expect(screen.getByText("2 of 2")).toBeVisible();
    expect(screen.getByText("100%")).toBeVisible();
    expect(detailRequests).toBe(0);
    expect(screen.queryByText("DOqEJyxCRGJ")).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "View 1 warning" }),
    );

    expect(await screen.findByText("DOqEJyxCRGJ")).toBeVisible();
    expect(screen.getByText("Reel")).toBeVisible();
    expect(screen.getByText("instagram_unavailable")).toBeVisible();
    expect(
      screen.getByText("Instagram could not be reached. Try again later."),
    ).toBeVisible();
    expect(
      screen.getByText("BadResponseException → ConnectionException"),
    ).toBeVisible();
    expect(screen.getByText(/July 31, 2026/)).toBeVisible();
    expect(detailRequests).toBe(1);
    expect(screen.queryByText(/sessionid=/)).not.toBeInTheDocument();
    expect(screen.queryByText(/__a=1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
    expect(screen.queryByText(/stealCookies/)).not.toBeInTheDocument();
  });

  it("keeps the warning summary when detail loading fails", async () => {
    server.use(
      http.get("/api/jobs", () =>
        HttpResponse.json(successEnvelope([
          jobFixture({
            state: "completed_with_warnings",
            progress_current: 1,
            progress_total: 1,
            status_text: "Finished with one warning.",
            phase: "completed",
            issue_count: 1,
            completed_at: "2026-07-31T08:00:04Z",
          }),
        ])),
      ),
      http.get("/api/jobs/job-1", () =>
        HttpResponse.json(
          {
            success: false,
            data: null,
            error: {
              code: "job_detail_unavailable",
              message: "Traceback: request failed for /?sessionid=secret&__a=1",
            },
            meta: {},
          },
          { status: 503 },
        ),
      ),
    );
    render(
      <TestRouter
        initialPath="/activity"
        initialSession={authenticatedSession}
      />,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "View 1 warning" }),
    );

    expect(
      await screen.findByRole("alert"),
    ).toHaveTextContent("Warning details could not be loaded. Please try again.");
    expect(screen.getByText("Completed with warnings")).toBeVisible();
    expect(screen.getByText("Finished with one warning.")).toBeVisible();
    expect(screen.getByRole("button", { name: /warning/i })).toBeVisible();
    expect(screen.queryByText(/sessionid=/)).not.toBeInTheDocument();
    expect(screen.queryByText(/__a=1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
  });
});
