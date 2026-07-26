import { HttpResponse, http } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";
import { LoginPage } from "./LoginPage";

describe("LoginPage", () => {
  it("submits credentials and routes a bootstrap admin to password change", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          success: true,
          data: {
            username: "owner",
            must_change_password: true,
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
    await user.type(
      screen.getByLabelText("Password"),
      "correct-horse-battery-staple",
    );
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByRole("heading", { name: "Change your password" }),
    ).toBeVisible();
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
          csrf_token: "csrf",
        }}
      >
        <LoginPage />
      </TestRouter>,
    );

    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });
});
