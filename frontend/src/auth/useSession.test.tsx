import { useState } from "react";
import { HttpResponse, http } from "msw";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";
import { type SessionData, useSession } from "./useSession";

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

type TerminalLogoutProbeProps = {
  runSessionOperation: () => Promise<SessionData>;
};

function TerminalLogoutProbe({
  runSessionOperation,
}: TerminalLogoutProbeProps) {
  const {
    applySessionOperation,
    logout,
    logoutPending,
    refreshSession,
    session,
    setSession,
    status,
  } = useSession();
  const [operationOutcome, setOperationOutcome] = useState("pending");

  return (
    <div>
      <span aria-label="session status">{status}</span>
      <span aria-label="session username">{session?.username ?? "none"}</span>
      <span aria-label="operation outcome">{operationOutcome}</span>
      <button type="button" onClick={() => void logout()}>
        {logoutPending ? "Logout pending" : "Begin logout"}
      </button>
      <button
        type="button"
        onClick={() =>
          setSession({
            username: "set-owner",
            must_change_password: false,
            expires_at: "2026-08-04T00:00:00Z",
            csrf_token: "e".repeat(64),
          })
        }
      >
        Set session
      </button>
      <button type="button" onClick={() => void refreshSession()}>
        Refresh during logout
      </button>
      <button
        type="button"
        onClick={() =>
          void applySessionOperation(runSessionOperation).then((result) => {
            setOperationOutcome(result === null ? "blocked" : "applied");
          })
        }
      >
        Apply session operation
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

it("keeps a pending logout terminal against later session-producing operations", async () => {
  let releaseLogout: (() => void) | undefined;
  let logoutStarted = false;
  let refreshRequests = 0;
  let sessionOperations = 0;
  const logoutGate = new Promise<void>((resolve) => {
    releaseLogout = resolve;
  });
  server.use(
    http.post("/api/auth/logout", async () => {
      logoutStarted = true;
      await logoutGate;
      return HttpResponse.json({
        success: true,
        data: { logged_out: true },
        error: null,
        meta: {},
      });
    }),
    http.get("/api/auth/session", () => {
      refreshRequests += 1;
      return HttpResponse.json({
        success: true,
        data: {
          username: "refreshed-owner",
          must_change_password: false,
          expires_at: "2026-08-04T00:00:00Z",
          csrf_token: "f".repeat(64),
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
      <TerminalLogoutProbe
        runSessionOperation={() => {
          sessionOperations += 1;
          return Promise.resolve({
            username: "operation-owner",
            must_change_password: false,
            expires_at: "2026-08-04T00:00:00Z",
            csrf_token: "a".repeat(64),
          });
        }}
      />
    </TestRouter>,
  );

  await userEvent.click(screen.getByRole("button", { name: "Begin logout" }));
  await waitFor(() => expect(logoutStarted).toBe(true));
  expect(
    screen.getByRole("button", { name: "Logout pending" }),
  ).toBeVisible();

  await userEvent.click(screen.getByRole("button", { name: "Set session" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh during logout" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Apply session operation" }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("operation outcome")).not.toHaveTextContent(
      "pending",
    ),
  );

  releaseLogout?.();

  await waitFor(() =>
    expect(screen.getByLabelText("session status")).toHaveTextContent(
      "unauthenticated",
    ),
  );
  expect(screen.getByLabelText("session username")).toHaveTextContent("none");
  expect(screen.getByLabelText("operation outcome")).toHaveTextContent(
    "blocked",
  );
  expect(refreshRequests).toBe(0);
  expect(sessionOperations).toBe(0);
});
