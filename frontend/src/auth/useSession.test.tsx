import { HttpResponse, http } from "msw";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";
import { useSession } from "./useSession";

const CSRF_TOKEN = "c".repeat(64);
const RENEWED_CSRF_TOKEN = "d".repeat(64);

function SessionProbe() {
  const { session, status, refreshSession, setSession } = useSession();
  return (
    <div>
      <span>{status === "loading" ? "Refreshing" : session?.username}</span>
      <button type="button" onClick={() => void refreshSession()}>
        Refresh session
      </button>
      <button
        type="button"
        onClick={() =>
          setSession({
            username: "newer-owner",
            must_change_password: false,
            expires_at: "2026-08-04T00:00:00Z",
            csrf_token: "e".repeat(64),
          })
        }
      >
        Apply newer session
      </button>
    </div>
  );
}

it("refreshes the in-memory csrf-bearing session on demand", async () => {
  server.use(
    http.get("/api/auth/session", () =>
      HttpResponse.json({
        success: true,
        data: {
          username: "renewed-owner",
          must_change_password: false,
          expires_at: "2026-08-03T00:00:00Z",
          csrf_token: RENEWED_CSRF_TOKEN,
        },
        error: null,
        meta: {},
      }),
    ),
  );
  render(
    <TestRouter
      initialSession={{
        username: "owner",
        must_change_password: false,
        expires_at: "2026-08-02T00:00:00Z",
        csrf_token: CSRF_TOKEN,
      }}
    >
      <SessionProbe />
    </TestRouter>,
  );

  await userEvent.click(
    screen.getByRole("button", { name: "Refresh session" }),
  );

  expect(await screen.findByText("renewed-owner")).toBeVisible();
});

it("does not let an older refresh overwrite a later auth transition", async () => {
  let refreshStarted = false;
  let releaseRefresh: (() => void) | undefined;
  const refreshGate = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });
  server.use(
    http.get("/api/auth/session", async () => {
      refreshStarted = true;
      await refreshGate;
      return HttpResponse.json({
        success: true,
        data: {
          username: "stale-owner",
          must_change_password: false,
          expires_at: "2026-08-03T00:00:00Z",
          csrf_token: "d".repeat(64),
        },
        error: null,
        meta: {},
      });
    }),
  );
  render(
    <TestRouter
      initialSession={{
        username: "owner",
        must_change_password: false,
        expires_at: "2026-08-02T00:00:00Z",
        csrf_token: CSRF_TOKEN,
      }}
    >
      <SessionProbe />
    </TestRouter>,
  );

  await userEvent.click(
    screen.getByRole("button", { name: "Refresh session" }),
  );
  await waitFor(() => expect(refreshStarted).toBe(true));
  await userEvent.click(
    screen.getByRole("button", { name: "Apply newer session" }),
  );
  expect(screen.getByText("newer-owner")).toBeVisible();

  await act(async () => {
    releaseRefresh?.();
    await refreshGate;
    await Promise.resolve();
  });

  expect(screen.getByText("newer-owner")).toBeVisible();
  expect(screen.queryByText("stale-owner")).not.toBeInTheDocument();
});
