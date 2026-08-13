import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import App from "../src/App.svelte";
import { session } from "../src/lib/session.svelte.js";
import { viewport } from "../src/lib/viewport.svelte.js";

beforeEach(() => {
  session.clear();
  vi.stubGlobal("fetch", () =>
    Promise.resolve({ ok: true, status: 200,
      json: () => Promise.resolve({ devices: [], rules: [], orphaned: [], proposals: [], entries: [] }) }));
});

function signIn(role = "admin") {
  session.username = "mani";
  session.role = role;
  session.signedIn = true;
}

describe("app", () => {
  it("shows login when signed out and no tab bar", () => {
    render(App);
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("does not show the composer to a signed-out visitor", () => {
    render(App);
    expect(screen.queryByLabelText(/tell the house what to do/i)).toBeNull();
  });

  it("shows the shell once signed in", async () => {
    render(App);
    signIn();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
  });

  it("shows the composer on every tab", async () => {
    render(App);
    signIn();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());

    for (const tab of ["House", "Rules", "Activity", "Settings"]) {
      await fireEvent.click(screen.getByRole("tab", { name: tab }));
      expect(screen.getByLabelText(/tell the house what to do/i)).toBeInTheDocument();
    }
  });

  it("the composer is never one of the tabs", async () => {
    render(App);
    signIn();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    expect(screen.queryByRole("tab", { name: /ask|chat|assistant|compose/i })).toBeNull();
  });

  it("switches to Rules when an instruction compiles into a proposal", async () => {
    render(App);
    signIn();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());

    vi.stubGlobal("fetch", (path) => {
      if (path === "/api/drishti/instruct") {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({
          lane: "compile", ok: true, resolved: "compiled", proposal_id: "p1",
          rule: { source_utterance: "turn the fan off when the room is empty" },
          rendered: { when: "occupancy == empty", then: "fan → off" }, conflict: null,
        }) });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({
        devices: [], rules: [], orphaned: [], proposals: [], entries: [],
      }) });
    });

    await fireEvent.input(screen.getByLabelText(/tell the house what to do/i),
      { target: { value: "turn the fan off when the room is empty" } });
    await fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Rules" })).toHaveAttribute("aria-selected", "true"));
  });

  it("stays put when the answer came from the device", async () => {
    render(App);
    signIn();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());

    vi.stubGlobal("fetch", (path) => {
      if (path === "/api/drishti/instruct") {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({
          lane: "local", ok: true, kind: "state", text: "Nobody is home.", resolved: "on-device",
        }) });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({
        devices: [], rules: [], orphaned: [], proposals: [], entries: [],
      }) });
    });

    await fireEvent.input(screen.getByLabelText(/tell the house what to do/i),
      { target: { value: "is anyone home?" } });
    await fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await screen.findByText(/nobody is home/i);
    expect(screen.getByRole("tab", { name: "House" })).toHaveAttribute("aria-selected", "true");
  });

  it("never renders a message transcript", async () => {
    render(App);
    signIn();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    expect(screen.queryByRole("log")).toBeNull();
    expect(document.querySelector("[class*='transcript'], [class*='messages']")).toBeNull();
  });

  it("keeps Settings for a non-admin and changes only its contents", async () => {
    render(App);
    signIn("user");
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());

    await fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: /account/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^people$/i })).toBeNull();
  });
});

describe("shell selection", () => {
  it("mounts one shell and never both", async () => {
    // Both shells expose role="tablist". Two would mean the phone design and
    // the laptop design were on screen at the same time.
    viewport.isDesktop = false;
    signIn();
    const { container } = render(App);
    await waitFor(() =>
      expect(container.querySelectorAll('[role="tablist"]')).toHaveLength(1));
    expect(screen.queryByText("Drishti")).toBeNull();
  });

  it("shows the wordmark only on the desktop shell", async () => {
    viewport.isDesktop = true;
    signIn();
    render(App);
    await waitFor(() => expect(screen.getByText("Drishti")).toBeInTheDocument());
    viewport.isDesktop = false;
  });
});
