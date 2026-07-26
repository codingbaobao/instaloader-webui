import { HttpResponse, http } from "msw";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";

const CSRF_TOKEN = "c".repeat(64);

describe("ChangePasswordPage", () => {
  it("sends the csrf token and enters the authenticated shell", async () => {
    let csrfHeader = "";
    let submittedPasswords: unknown;
    server.use(
      http.post("/api/auth/change-password", async ({ request }) => {
        csrfHeader = request.headers.get("X-CSRF-Token") ?? "";
        submittedPasswords = await request.json();
        return HttpResponse.json({
          success: true,
          data: {
            username: "owner",
            must_change_password: false,
            expires_at: "2026-08-02T00:00:00Z",
            csrf_token: CSRF_TOKEN,
          },
          error: null,
          meta: {},
        });
      }),
    );

    render(
      <TestRouter
        initialPath="/change-password"
        initialSession={{
          username: "owner",
          must_change_password: true,
          expires_at: "2026-08-02T00:00:00Z",
          csrf_token: CSRF_TOKEN,
        }}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(
      await screen.findByRole("navigation", { name: "Desktop" }),
    ).toBeVisible();
    expect(screen.getAllByText("Profiles").length).toBeGreaterThan(0);
    expect(csrfHeader).toBe(CSRF_TOKEN);
    expect(submittedPasswords).toEqual({
      current_password: "",
      new_password: "",
    });
  });

  it("validates password confirmation before sending credentials", async () => {
    let requests = 0;
    server.use(
      http.post("/api/auth/change-password", () => {
        requests += 1;
        return HttpResponse.json({});
      }),
    );
    render(
      <TestRouter
        initialPath="/change-password"
        initialSession={{
          username: "owner",
          must_change_password: true,
          expires_at: "2026-08-02T00:00:00Z",
          csrf_token: CSRF_TOKEN,
        }}
      />,
    );

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Current password"), "old-password");
    await user.type(
      screen.getByLabelText("New password"),
      "different-long-owner-password",
    );
    await user.type(
      screen.getByLabelText("Confirm new password"),
      "not-the-same-password",
    );
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText("The new passwords do not match.")).toBeVisible();
    expect(requests).toBe(0);
  });

  it("shows a safe API error and permits a retry", async () => {
    server.use(
      http.post("/api/auth/change-password", () =>
        HttpResponse.json(
          {
            success: false,
            data: null,
            error: {
              code: "invalid_current_password",
              message: "The current password is incorrect.",
            },
            meta: {},
          },
          { status: 401 },
        ),
      ),
    );
    render(
      <TestRouter
        initialPath="/change-password"
        initialSession={{
          username: "owner",
          must_change_password: true,
          expires_at: "2026-08-02T00:00:00Z",
          csrf_token: CSRF_TOKEN,
        }}
      />,
    );

    const user = userEvent.setup();
    await user.type(
      screen.getByLabelText("Current password"),
      "incorrect-current-password",
    );
    await user.type(
      screen.getByLabelText("New password"),
      "different-long-owner-password",
    );
    await user.type(
      screen.getByLabelText("Confirm new password"),
      "different-long-owner-password",
    );
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(
      await screen.findByText("The current password is incorrect."),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Change password" }),
    ).toBeEnabled();
    expect(screen.getByLabelText("Current password")).toHaveValue("");
  });

  it("offers duplicate-safe csrf-protected logout during forced password change", async () => {
    let csrfHeader = "";
    let requestCount = 0;
    let releaseResponse: (() => void) | undefined;
    const responseGate = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    server.use(
      http.post("/api/auth/logout", async ({ request }) => {
        requestCount += 1;
        csrfHeader = request.headers.get("X-CSRF-Token") ?? "";
        await responseGate;
        return HttpResponse.json({
          success: true,
          data: { logged_out: true },
          error: null,
          meta: {},
        });
      }),
    );
    render(
      <TestRouter
        initialPath="/change-password"
        initialSession={{
          username: "owner",
          must_change_password: true,
          expires_at: "2026-08-02T00:00:00Z",
          csrf_token: CSRF_TOKEN,
        }}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(
      await screen.findByRole("button", { name: "Signing out…" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Signing out…" }),
    );
    expect(requestCount).toBe(1);
    releaseResponse?.();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(csrfHeader).toBe(CSRF_TOKEN);
  });

  it("does not restore a stale password-change session after logout", async () => {
    let releaseChange: (() => void) | undefined;
    let changeStarted = false;
    let changeCompleted = false;
    const changeGate = new Promise<void>((resolve) => {
      releaseChange = resolve;
    });
    server.use(
      http.post("/api/auth/change-password", async () => {
        changeStarted = true;
        await changeGate;
        changeCompleted = true;
        return HttpResponse.json({
          success: true,
          data: {
            username: "owner",
            must_change_password: false,
            expires_at: "2026-08-03T00:00:00Z",
            csrf_token: "d".repeat(64),
          },
          error: null,
          meta: {},
        });
      }),
      http.post("/api/auth/logout", () =>
        HttpResponse.json({
          success: true,
          data: { logged_out: true },
          error: null,
          meta: {},
        }),
      ),
    );
    render(
      <TestRouter
        initialPath="/change-password"
        initialSession={{
          username: "owner",
          must_change_password: true,
          expires_at: "2026-08-02T00:00:00Z",
          csrf_token: CSRF_TOKEN,
        }}
      />,
    );

    const user = userEvent.setup();
    await user.type(
      screen.getByLabelText("Current password"),
      "initial-password-value",
    );
    await user.type(
      screen.getByLabelText("New password"),
      "different-long-owner-password",
    );
    await user.type(
      screen.getByLabelText("Confirm new password"),
      "different-long-owner-password",
    );
    await user.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(changeStarted).toBe(true));

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();

    await act(async () => {
      releaseChange?.();
      await changeGate;
      await Promise.resolve();
    });
    await waitFor(() => expect(changeCompleted).toBe(true));

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Desktop" })).not.toBeInTheDocument();
    expect(
      screen.queryByText("The password could not be changed."),
    ).not.toBeInTheDocument();
  });

  it("blocks password changes that are submitted after logout begins", async () => {
    let changeRequests = 0;
    let logoutStarted = false;
    let releaseLogout: (() => void) | undefined;
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
      http.post("/api/auth/change-password", () => {
        changeRequests += 1;
        return HttpResponse.json({
          success: true,
          data: {
            username: "owner",
            must_change_password: false,
            expires_at: "2026-08-04T00:00:00Z",
            csrf_token: "d".repeat(64),
          },
          error: null,
          meta: {},
        });
      }),
    );
    render(
      <TestRouter
        initialPath="/change-password"
        initialSession={{
          username: "owner",
          must_change_password: true,
          expires_at: "2026-08-02T00:00:00Z",
          csrf_token: CSRF_TOKEN,
        }}
      />,
    );

    const user = userEvent.setup();
    await user.type(
      screen.getByLabelText("Current password"),
      "initial-password-value",
    );
    await user.type(
      screen.getByLabelText("New password"),
      "different-long-owner-password",
    );
    await user.type(
      screen.getByLabelText("Confirm new password"),
      "different-long-owner-password",
    );
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(logoutStarted).toBe(true));

    const changeButton = screen.getByRole("button", {
      name: "Change password",
    });
    const changeForm = changeButton.closest("form");
    expect(changeForm).not.toBeNull();
    fireEvent.submit(changeForm as HTMLFormElement);
    const disabledWhileLogoutPending = changeButton.hasAttribute("disabled");

    releaseLogout?.();

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(disabledWhileLogoutPending).toBe(true);
    expect(changeRequests).toBe(0);
    expect(
      screen.queryByRole("navigation", { name: "Desktop" }),
    ).not.toBeInTheDocument();
  });
});
