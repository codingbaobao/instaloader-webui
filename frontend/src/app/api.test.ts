import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "./api";
import { server } from "../test/server";

describe("apiRequest", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends JSON with credentials and a csrf header", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          data: { accepted: true },
          error: null,
          meta: {},
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await apiRequest<{ accepted: boolean }>("/api/example", {
      method: "POST",
      body: { value: "safe" },
      csrfToken: "csrf-value",
    });

    const request = fetchSpy.mock.calls[0]?.[1];
    expect(request?.credentials).toBe("include");
    expect(new Headers(request?.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(new Headers(request?.headers).get("X-CSRF-Token")).toBe("csrf-value");
    expect(request?.body).toBe('{"value":"safe"}');
  });

  it("throws only the stable code and safe server message", async () => {
    server.use(
      http.get("/api/example", () =>
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

    await expect(apiRequest("/api/example")).rejects.toEqual(
      new ApiError("authentication_required", "Authentication is required."),
    );
  });

  it("normalizes malformed service responses", async () => {
    server.use(
      http.get("/api/example", () =>
        HttpResponse.json({ unexpected: true }, { status: 200 }),
      ),
    );

    await expect(apiRequest("/api/example")).rejects.toEqual(
      new ApiError(
        "invalid_response",
        "The service returned an invalid response.",
      ),
    );
  });

  it("normalizes non-JSON service responses", async () => {
    server.use(
      http.get(
        "/api/example",
        () =>
          new HttpResponse("not-json", {
            status: 502,
            headers: { "Content-Type": "text/plain" },
          }),
      ),
    );

    await expect(apiRequest("/api/example")).rejects.toEqual(
      new ApiError(
        "invalid_response",
        "The service returned an invalid response.",
      ),
    );
  });

  it("normalizes network failures", async () => {
    server.use(http.get("/api/example", () => HttpResponse.error()));

    await expect(apiRequest("/api/example")).rejects.toEqual(
      new ApiError(
        "network_error",
        "The service could not be reached. Please try again.",
      ),
    );
  });
});
