import { ApiError, apiRequest } from "../app/api";

export type SessionData = {
  username: string;
  must_change_password: boolean;
  expires_at: string;
  csrf_token: string;
};

type SessionRequestOptions = Parameters<typeof apiRequest>[1];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseSessionData(value: unknown): SessionData {
  if (
    !isRecord(value) ||
    typeof value.username !== "string" ||
    value.username.length < 1 ||
    value.username.length > 64 ||
    typeof value.must_change_password !== "boolean" ||
    typeof value.expires_at !== "string" ||
    Number.isNaN(Date.parse(value.expires_at)) ||
    typeof value.csrf_token !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.csrf_token)
  ) {
    throw new ApiError(
      "invalid_response",
      "The service returned an invalid response.",
    );
  }

  return {
    username: value.username,
    must_change_password: value.must_change_password,
    expires_at: value.expires_at,
    csrf_token: value.csrf_token,
  };
}

export async function requestSession(
  path: string,
  options?: SessionRequestOptions,
): Promise<SessionData> {
  return parseSessionData(await apiRequest<unknown>(path, options));
}
