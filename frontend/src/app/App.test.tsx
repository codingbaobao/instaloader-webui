import { HttpResponse, http } from "msw";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { server } from "../test/server";

const CSRF_TOKEN = "c".repeat(64);
const authenticatedSession = {
  username: "owner",
  must_change_password: false,
  expires_at: "2026-08-02T00:00:00Z",
  csrf_token: CSRF_TOKEN,
};

describe("App", () => {
  it("renders mobile bottom navigation and desktop navigation landmarks", () => {
    render(<App initialSession={authenticatedSession} />);

    expect(screen.getByRole("navigation", { name: "Mobile" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Desktop" })).toBeInTheDocument();
    expect(screen.getAllByText("Profiles").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Activity").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Settings").length).toBeGreaterThan(0);
    expect(
      within(screen.getByLabelText("Mobile account controls")).getByRole(
        "button",
        { name: "Sign out" },
      ),
    ).toBeEnabled();
  });

  it("reacquires the session from the API after reload", async () => {
    server.use(
      http.get("/api/auth/session", () =>
        HttpResponse.json({
          success: true,
          data: authenticatedSession,
          error: null,
          meta: {},
        }),
      ),
    );

    render(<App />);

    expect(await screen.findByText("Welcome back, owner")).toBeVisible();
  });

  it("routes to sign in when no browser session can be reacquired", async () => {
    server.use(
      http.get("/api/auth/session", () =>
        HttpResponse.json(
          {
            success: false,
            data: null,
            error: {
              code: "authentication_required",
              message: "Authentication is required.",
            },
            meta: {},
          },
          { status: 401 },
        ),
      ),
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  it("shows a retry state for a network error and can recover", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/auth/session", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.error()
          : HttpResponse.json({
              success: true,
              data: authenticatedSession,
              error: null,
              meta: {},
            });
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "Unable to restore your session",
      }),
    ).toBeVisible();
    expect(
      screen.getByText("The service could not be reached. Please try again."),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Welcome back, owner")).toBeVisible();
  });

  it("shows a retry state for a safe server error", async () => {
    server.use(
      http.get("/api/auth/session", () =>
        HttpResponse.json(
          {
            success: false,
            data: null,
            error: {
              code: "service_unavailable",
              message: "The session service is temporarily unavailable.",
            },
            meta: {},
          },
          { status: 503 },
        ),
      ),
    );

    render(<App />);

    expect(
      await screen.findByText(
        "The session service is temporarily unavailable.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("rejects a malformed successful session without crashing the shell", async () => {
    server.use(
      http.get("/api/auth/session", () =>
        HttpResponse.json({
          success: true,
          data: {
            username: 42,
            must_change_password: false,
            expires_at: "not-a-date",
            csrf_token: null,
          },
          error: null,
          meta: {},
        }),
      ),
    );

    render(<App />);

    expect(
      await screen.findByText("The service returned an invalid response."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByRole("navigation", { name: "Desktop" })).not.toBeInTheDocument();
  });

  it("logs out with csrf and returns to login", async () => {
    let csrfHeader = "";
    server.use(
      http.post("/api/auth/logout", ({ request }) => {
        csrfHeader = request.headers.get("X-CSRF-Token") ?? "";
        return HttpResponse.json({
          success: true,
          data: { logged_out: true },
          error: null,
          meta: {},
        });
      }),
    );
    render(<App initialSession={authenticatedSession} />);

    await userEvent.click(
      within(screen.getByLabelText("Desktop account controls")).getByRole(
        "button",
        { name: "Sign out" },
      ),
    );

    await waitFor(() => expect(csrfHeader).toBe(CSRF_TOKEN));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  it("keeps the session and shows a safe error when logout fails", async () => {
    server.use(
      http.post("/api/auth/logout", () =>
        HttpResponse.json(
          {
            success: false,
            data: null,
            error: {
              code: "request_failed",
              message: "Sign out could not be completed.",
            },
            meta: {},
          },
          { status: 503 },
        ),
      ),
    );
    render(<App initialSession={authenticatedSession} />);

    await userEvent.click(
      within(screen.getByLabelText("Desktop account controls")).getByRole(
        "button",
        { name: "Sign out" },
      ),
    );

    expect(
      await screen.findByText("Sign out could not be completed."),
    ).toBeVisible();
    expect(screen.getByText("Welcome back, owner")).toBeVisible();
    expect(
      within(screen.getByLabelText("Desktop account controls")).getByRole(
        "button",
        { name: "Sign out" },
      ),
    ).toBeEnabled();
  });

  it("clears a stale local session when logout reports authentication required", async () => {
    server.use(
      http.post("/api/auth/logout", () =>
        HttpResponse.json(
          {
            success: false,
            data: null,
            error: {
              code: "authentication_required",
              message: "Authentication is required.",
            },
            meta: {},
          },
          { status: 401 },
        ),
      ),
    );
    render(<App initialSession={authenticatedSession} />);

    await userEvent.click(
      within(screen.getByLabelText("Mobile account controls")).getByRole(
        "button",
        { name: "Sign out" },
      ),
    );

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
  });
});
