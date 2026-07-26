import { HttpResponse, http } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";
import { LoginPage } from "./LoginPage";

const CSRF_TOKEN = "c".repeat(64);

describe("LoginPage", () => {
  it("submits credentials and routes a bootstrap admin to password change", async () => {
    let submittedPassword: unknown;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        const body = (await request.json()) as { password?: unknown };
        submittedPassword = body.password;
        return HttpResponse.json({
          success: true,
          data: {
            username: "owner",
            must_change_password: true,
            expires_at: "2026-08-02T00:00:00Z",
            csrf_token: CSRF_TOKEN,
          },
          error: null,
          meta: {},
        });
      }),
    );
    render(<TestRouter initialPath="/login" />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Username"), "owner");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByRole("heading", { name: "Change your password" }),
    ).toBeVisible();
    expect(submittedPassword).toBe("");
  });

  it("shows a safe error and re-enables submission after rejected credentials", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json(
          {
            success: false,
            data: null,
            error: {
              code: "invalid_credentials",
              message: "The username or password is incorrect.",
            },
            meta: {},
          },
          { status: 401 },
        ),
      ),
    );
    render(<TestRouter initialPath="/login" />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Username"), "owner");
    await user.type(screen.getByLabelText("Password"), "incorrect-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByText("The username or password is incorrect."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("does not show the login form to an authenticated administrator", () => {
    render(
      <TestRouter
        initialPath="/login"
        initialSession={{
          username: "owner",
          must_change_password: false,
          expires_at: "2026-08-02T00:00:00Z",
          csrf_token: CSRF_TOKEN,
        }}
      >
        <LoginPage />
      </TestRouter>,
    );

    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("rejects malformed successful session data without entering the shell", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          success: true,
          data: {
            username: { unsafe: true },
            must_change_password: false,
            expires_at: "2026-08-02T00:00:00Z",
            csrf_token: "csrf",
          },
          error: null,
          meta: {},
        }),
      ),
    );
    render(<TestRouter initialPath="/login" />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Username"), "owner");
    await user.type(screen.getByLabelText("Password"), "valid-long-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByText("The service returned an invalid response."),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Desktop" })).not.toBeInTheDocument();
  });
});
