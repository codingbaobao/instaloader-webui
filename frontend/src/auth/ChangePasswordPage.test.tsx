import { HttpResponse, http } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";

describe("ChangePasswordPage", () => {
  it("sends the csrf token and enters the authenticated shell", async () => {
    let csrfHeader = "";
    server.use(
      http.post("/api/auth/change-password", ({ request }) => {
        csrfHeader = request.headers.get("X-CSRF-Token") ?? "";
        return HttpResponse.json({
          success: true,
          data: {
            username: "owner",
            must_change_password: false,
            expires_at: "2026-08-02T00:00:00Z",
            csrf_token: "csrf-value",
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
          csrf_token: "csrf-value",
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

    expect(
      await screen.findByRole("navigation", { name: "Desktop" }),
    ).toBeVisible();
    expect(screen.getAllByText("Profiles").length).toBeGreaterThan(0);
    expect(csrfHeader).toBe("csrf-value");
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
          csrf_token: "csrf-value",
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
          csrf_token: "csrf-value",
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
});
