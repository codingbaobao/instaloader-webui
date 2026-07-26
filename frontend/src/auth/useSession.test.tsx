import { HttpResponse, http } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { TestRouter } from "../test/TestRouter";
import { server } from "../test/server";
import { useSession } from "./useSession";

function SessionProbe() {
  const { session, loading, refreshSession } = useSession();
  return (
    <div>
      <span>{loading ? "Refreshing" : session?.username}</span>
      <button type="button" onClick={() => void refreshSession()}>
        Refresh session
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
          csrf_token: "renewed-csrf",
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
        csrf_token: "csrf",
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
