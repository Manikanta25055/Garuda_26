import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import Settings from "../src/routes/Settings.svelte";
import { session } from "../src/lib/session.svelte.js";
import { house } from "../src/lib/app.svelte.js";

beforeEach(() => {
  session.username = "mani";
  session.role = "admin";
  house.state = {
    pipeline: "running",
    rule_loop: { running: true, ticks: 42, fires: 3, rules: 2, orphaned_rules: 1,
                 last_error: "KeyError: 'occupancy'" },
  };
  vi.stubGlobal("fetch", () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({ devices: [] }),
  }));
});

describe("settings is for the person living here", () => {
  it("shows nothing about the rule loop, even to an admin", async () => {
    // Tick counts, fire counts and orphan counts were written so whoever built
    // this could tell the loop was alive. A resident never asks how many times
    // it evaluated. The state is deliberately populated above, so this fails if
    // the readout comes back.
    render(Settings);
    await waitFor(() => expect(screen.getByRole("heading", { name: /^devices$/i })).toBeInTheDocument());
    expect(screen.queryByText(/rule loop|evaluated|actions taken/i)).toBeNull();
    expect(screen.queryByText(/\b42\b|\bticks\b/i)).toBeNull();
  });

  it("does not surface a swallowed exception to someone who cannot act on it", () => {
    render(Settings);
    expect(screen.queryByText(/KeyError/)).toBeNull();
  });

  it("has no heading that leads nowhere", async () => {
    // People, Alerts and Automation were three headings with one paragraph each
    // and nothing behind them. A heading that does nothing teaches the reader
    // that headings here do nothing.
    render(Settings);
    await waitFor(() => expect(screen.getByRole("heading", { name: /^devices$/i })).toBeInTheDocument());
    for (const dead of [/^people$/i, /^alerts$/i, /^automation$/i, /^system$/i]) {
      expect(screen.queryByRole("heading", { name: dead })).toBeNull();
    }
  });

  it("keeps the two things a resident can actually change", async () => {
    render(Settings);
    await waitFor(() => expect(screen.getByRole("heading", { name: /^devices$/i })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /add a device/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
  });

  it("still says what leaves the house when a rule is taught", async () => {
    // The one piece of prose that earns its place: it is about the user's data,
    // not about the implementation.
    render(Settings);
    await waitFor(() => expect(screen.getByRole("heading", { name: /privacy/i })).toBeInTheDocument());
    expect(screen.getByText(/never sends a camera frame/i)).toBeInTheDocument();
  });

  it("shows the same screen to a non-admin", async () => {
    // The screen no longer changes shape by role, because nothing left on it is
    // privileged.
    session.role = "user";
    render(Settings);
    await waitFor(() => expect(screen.getByRole("heading", { name: /^devices$/i })).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: /account/i })).toBeInTheDocument();
  });
});
