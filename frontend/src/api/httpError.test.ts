/// <reference types="vitest" />
import { describe, it, expect } from "vitest";
import { ApiError, httpResponseToError, parseRetryAfter } from "./types";

/** Build a fake Response object with the given status, headers, and body. */
function fakeResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    statusText: "",
    headers: {
      get(name: string) {
        return headers[name] ?? null;
      },
    },
    json: async () => body,
  } as unknown as Response;
}

describe("parseRetryAfter", () => {
  it("returns the integer for delta-seconds values", () => {
    expect(parseRetryAfter("5")).toBe(5);
    expect(parseRetryAfter("0")).toBe(0);
    expect(parseRetryAfter("120")).toBe(120);
  });

  it("returns undefined for missing/garbage", () => {
    expect(parseRetryAfter(null)).toBeUndefined();
    expect(parseRetryAfter(undefined)).toBeUndefined();
    expect(parseRetryAfter("")).toBeUndefined();
    expect(parseRetryAfter("garbage")).toBeUndefined();
    expect(parseRetryAfter("-3")).toBeUndefined();
  });
});

describe("httpResponseToError", () => {
  it("surfaces 503 + FastAPI {detail} as a real error (not undefined)", () => {
    // The bug this guards against: a non-2xx with {detail:"..."} was
    // silently returning undefined to the caller, masking the failure
    // as 'no data' instead of a 503.
    const r = fakeResponse(503, { detail: "trigger queue full" });
    const err = httpResponseToError(r, { detail: "trigger queue full" });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(503);
    expect(err.code).toBe("http_503");
    expect(err.message).toContain("trigger queue full");
  });

  it("attaches Retry-After when present on a 5xx", () => {
    const r = fakeResponse(
      503,
      { detail: "trigger queue full" },
      { "Retry-After": "5" },
    );
    const err = httpResponseToError(r, { detail: "trigger queue full" });
    expect(err.retry_after_seconds).toBe(5);
  });

  it("prefers the envelope {error:{code,message}} when both shapes are present", () => {
    const r = fakeResponse(400, {
      data: null,
      error: { code: "bad_request", message: "field X is required" },
    });
    const err = httpResponseToError(r, {
      error: { code: "bad_request", message: "field X is required" },
    });
    expect(err.code).toBe("bad_request");
    expect(err.message).toContain("field X is required");
    expect(err.status).toBe(400);
  });

  it("falls back to a useful message when body has no detail/error", () => {
    const r = fakeResponse(500, { something: "else" });
    const err = httpResponseToError(r, { something: "else" } as any);
    expect(err.status).toBe(500);
    // The message can come from r.statusText OR a generic `HTTP 500`
    // — just ensure it's non-empty and includes the status number.
    expect(err.message).toBeTruthy();
    expect(err.message).toContain("500");
  });

  it("handles 204 / empty body gracefully", () => {
    const r = fakeResponse(503, null);
    const err = httpResponseToError(r, null);
    expect(err.status).toBe(503);
  });
});
