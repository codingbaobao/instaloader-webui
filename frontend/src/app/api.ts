export type ApiEnvelope<T> = {
  success: boolean;
  data: T | null;
  error: { code: string; message: string } | null;
  meta: Record<string, unknown>;
};

type ApiRequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  csrfToken?: string;
  signal?: AbortSignal;
};

export class ApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

function isEnvelope<T>(value: unknown): value is ApiEnvelope<T> {
  return (
    typeof value === "object" &&
    value !== null &&
    "success" in value &&
    typeof value.success === "boolean" &&
    "data" in value &&
    "error" in value
  );
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const headers = new Headers();
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (options.csrfToken) {
    headers.set("X-CSRF-Token", options.csrfToken);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      credentials: "include",
      headers,
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch {
    throw new ApiError(
      "network_error",
      "The service could not be reached. Please try again.",
    );
  }

  let envelope: unknown;
  try {
    envelope = await response.json();
  } catch {
    throw new ApiError(
      "invalid_response",
      "The service returned an invalid response.",
    );
  }

  if (!isEnvelope<T>(envelope)) {
    throw new ApiError(
      "invalid_response",
      "The service returned an invalid response.",
    );
  }
  if (!response.ok || !envelope.success || envelope.data === null) {
    throw new ApiError(
      envelope.error?.code ?? "request_failed",
      envelope.error?.message ?? "The request could not be completed.",
    );
  }
  return envelope.data;
}
