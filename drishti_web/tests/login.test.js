import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import Login from "../src/routes/Login.svelte";
import { session } from "../src/lib/session.svelte.js";

beforeEach(() => session.clear());

function respond(status, body) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

describe("login", () => {
  it("signs in and records the role", async () => {
    vi.stubGlobal("fetch", () => respond(200, { ok: true, username: "mani", role: "admin" }));

    render(Login);
    await fireEvent.input(screen.getByLabelText(/username/i), { target: { value: "mani" } });
    await fireEvent.input(screen.getByLabelText(/password/i), { target: { value: "pw" } });
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(session.signedIn).toBe(true));
    expect(session.role).toBe("admin");
    expect(session.username).toBe("mani");
  });

  it("sends what was typed, not what was rendered", async () => {
    const fetchMock = vi.fn(() => respond(200, { ok: true, username: "mani", role: "user" }));
    vi.stubGlobal("fetch", fetchMock);

    render(Login);
    await fireEvent.input(screen.getByLabelText(/username/i), { target: { value: "mani" } });
    await fireEvent.input(screen.getByLabelText(/password/i), { target: { value: "hunter2" } });
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/drishti/login");
    expect(JSON.parse(options.body)).toEqual({ username: "mani", password: "hunter2" });
  });

  it("shows the server's message on a bad password", async () => {
    vi.stubGlobal("fetch", () => respond(401, { detail: "invalid credentials" }));

    render(Login);
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid credentials/i);
    expect(session.signedIn).toBe(false);
  });

  it("says the house is unreachable when the network is down", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));

    render(Login);
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/can't reach the house/i);
  });

  it("clears a stale message when the next attempt starts", async () => {
    vi.stubGlobal("fetch", () => respond(401, { detail: "invalid credentials" }));
    render(Login);
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await screen.findByRole("alert");

    vi.stubGlobal("fetch", () => respond(200, { ok: true, username: "mani", role: "user" }));
    await fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("password field is a password input", () => {
    render(Login);
    expect(screen.getByLabelText(/password/i)).toHaveAttribute("type", "password");
  });

  it("offers the browser its password manager", () => {
    render(Login);
    expect(screen.getByLabelText(/username/i)).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText(/password/i)).toHaveAttribute("autocomplete", "current-password");
  });
});

describe("session", () => {
  it("signs out locally even when the server call fails", async () => {
    vi.stubGlobal("fetch", () => respond(200, { ok: true, username: "mani", role: "admin" }));
    await session.signIn("mani", "pw");
    expect(session.signedIn).toBe(true);

    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));
    await session.signOut();
    expect(session.signedIn).toBe(false);
    expect(session.username).toBe("");
  });
});
