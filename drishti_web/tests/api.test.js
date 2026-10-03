import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, ApiError, onUnauthorized } from "../src/lib/api.js";

function respond(status, body) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => { onUnauthorized(null); });

describe("api client", () => {
  it("calls a same-origin path and sends the cookie", async () => {
    const fetchMock = vi.fn(() => respond(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/api/drishti/devices");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/drishti/devices");
    expect(url.startsWith("http")).toBe(false);
    expect(options.credentials).toBe("same-origin");
  });

  it("posts JSON with the right content type", async () => {
    const fetchMock = vi.fn(() => respond(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api.post("/api/drishti/instruct", { text: "hello" });

    const [, options] = fetchMock.mock.calls[0];
    expect(options.method).toBe("POST");
    expect(options.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(options.body)).toEqual({ text: "hello" });
  });

  it("sends DELETE without a body", async () => {
    const fetchMock = vi.fn(() => respond(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api.del("/api/drishti/rules/abc");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/drishti/rules/abc");
    expect(options.method).toBe("DELETE");
    expect(options.body).toBeUndefined();
  });

  it("throws ApiError carrying the server's detail", async () => {
    vi.stubGlobal("fetch", () => respond(400, { detail: "channel 99 is not one of [1, 2, 3]" }));
    await expect(api.post("/api/drishti/devices", {})).rejects.toMatchObject({
      status: 400,
      detail: "channel 99 is not one of [1, 2, 3]",
    });
  });

  it("falls back to a readable message when the body is not JSON", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve({
      ok: false, status: 500, json: () => Promise.reject(new SyntaxError("no")),
    }));
    await expect(api.get("/api/drishti/rules")).rejects.toMatchObject({
      status: 500,
      detail: "Something went wrong.",
    });
  });

  it("fires the unauthorized handler on 401", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", () => respond(401, { detail: "not signed in" }));

    await expect(api.get("/api/drishti/rules")).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("fires the unauthorized handler once when a whole screen 401s at once", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", () => respond(401, { detail: "not signed in" }));

    await Promise.allSettled([
      api.get("/api/drishti/state"),
      api.get("/api/drishti/devices"),
      api.get("/api/drishti/rules"),
    ]);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("arms the handler again after a successful call", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);

    vi.stubGlobal("fetch", () => respond(401, {}));
    await expect(api.get("/api/drishti/rules")).rejects.toBeInstanceOf(ApiError);

    vi.stubGlobal("fetch", () => respond(200, { ok: true }));
    await api.get("/api/drishti/rules");

    vi.stubGlobal("fetch", () => respond(401, {}));
    await expect(api.get("/api/drishti/rules")).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(2);
  });

  it("does not treat a 403 as a sign-out", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", () => respond(403, { detail: "admin only" }));

    await expect(api.del("/api/drishti/devices/lamp")).rejects.toMatchObject({ status: 403 });
    expect(handler).not.toHaveBeenCalled();
  });

  it("reports a network failure as an offline ApiError", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));
    await expect(api.get("/api/drishti/state")).rejects.toMatchObject({
      status: 0,
      offline: true,
    });
  });

  it("does not sign the user out just because the house is unreachable", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));

    await expect(api.get("/api/drishti/state")).rejects.toMatchObject({ offline: true });
    expect(handler).not.toHaveBeenCalled();
  });
});
