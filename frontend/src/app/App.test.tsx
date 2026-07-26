import { HttpResponse, http } from "msw";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { server } from "../test/server";

const authenticatedSession = {
  username: "owner",
  must_change_password: false,
  expires_at: "2026-08-02T00:00:00Z",
  csrf_token: "csrf-value",
};

describe("App", () => {
  it("renders mobile bottom navigation and desktop navigation landmarks", () => {
    render(<App initialSession={authenticatedSession} />);

    expect(screen.getByRole("navigation", { name: "Mobile" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Desktop" })).toBeInTheDocument();
    expect(screen.getAllByText("Profiles").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Activity").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Settings").length).toBeGreaterThan(0);
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

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(csrfHeader).toBe("csrf-value"));
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

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(
      await screen.findByText("Sign out could not be completed."),
    ).toBeVisible();
    expect(screen.getByText("Welcome back, owner")).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });
});
