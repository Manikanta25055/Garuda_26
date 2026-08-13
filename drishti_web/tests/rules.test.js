import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/svelte";
import Rules from "../src/routes/Rules.svelte";

function stubRoutes(map, onCall) {
  vi.stubGlobal("fetch", (path, options) => {
    onCall?.(path, options);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(map[path] ?? {}) });
  });
}

const RULE = {
  id: "r_1", source_utterance: "turn the fan off when the room is empty",
  rendered: { when: "occupancy == empty", then: "fan → off" }, enabled: true,
};

const PROPOSAL = {
  id: "p1", rule: { source_utterance: "dim the lamp at ten" },
  rendered: { when: "hour >= 22", then: "lamp → off" }, conflict: null,
};

describe("rules screen", () => {
  it("shows pending proposals above saved rules", async () => {
    stubRoutes({
      "/api/drishti/rules": { rules: [RULE], orphaned: [] },
      "/api/drishti/proposals": { proposals: [PROPOSAL] },
      "/api/drishti/devices": { devices: [] },
    });

    render(Rules);

    await waitFor(() => expect(screen.getByText(/here's what i understood/i)).toBeInTheDocument());
    const headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings[0]).toHaveTextContent("dim the lamp at ten");
  });

  it("surfaces orphaned rules as needing attention", async () => {
    stubRoutes({
      "/api/drishti/rules": {
        rules: [], orphaned: [{ ...RULE, orphaned: true, enabled: false }],
      },
      "/api/drishti/proposals": { proposals: [] },
      "/api/drishti/devices": { devices: [] },
    });

    render(Rules);

    await waitFor(() => expect(screen.getByText(/needs repair/i)).toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/needs attention/i);
  });

  it("teaches on an empty rule base rather than showing a blank screen", async () => {
    stubRoutes({
      "/api/drishti/rules": { rules: [], orphaned: [] },
      "/api/drishti/proposals": { proposals: [] },
      "/api/drishti/devices": { devices: [] },
    });

    render(Rules);

    await waitFor(() =>
      expect(screen.getByText(/tell the house what to do/i)).toBeInTheDocument());
  });

  it("confirms a proposal through the confirm endpoint", async () => {
    const calls = [];
    stubRoutes({
      "/api/drishti/rules": { rules: [], orphaned: [] },
      "/api/drishti/proposals": { proposals: [PROPOSAL] },
      "/api/drishti/devices": { devices: [] },
    }, (path, options) => calls.push([options?.method ?? "GET", path]));

    render(Rules);
    await waitFor(() => expect(screen.getByRole("button", { name: /save/i })).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(calls).toContainEqual(["POST", "/api/drishti/proposals/p1/confirm"]));
  });

  it("discards a proposal through the delete endpoint", async () => {
    const calls = [];
    stubRoutes({
      "/api/drishti/rules": { rules: [], orphaned: [] },
      "/api/drishti/proposals": { proposals: [PROPOSAL] },
      "/api/drishti/devices": { devices: [] },
    }, (path, options) => calls.push([options?.method ?? "GET", path]));

    render(Rules);
    await waitFor(() => expect(screen.getByRole("button", { name: /discard/i })).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /discard/i }));

    await waitFor(() => expect(calls).toContainEqual(["DELETE", "/api/drishti/proposals/p1"]));
  });

  it("toggles and deletes a saved rule through their endpoints", async () => {
    const calls = [];
    stubRoutes({
      "/api/drishti/rules": { rules: [RULE], orphaned: [] },
      "/api/drishti/proposals": { proposals: [] },
      "/api/drishti/devices": { devices: [] },
    }, (path, options) => calls.push([options?.method ?? "GET", path]));

    render(Rules);
    await waitFor(() => expect(screen.getByRole("switch")).toBeInTheDocument());

    await fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(calls).toContainEqual(["POST", "/api/drishti/rules/r_1/toggle"]));

    await fireEvent.click(screen.getByRole("button", { name: /delete rule/i }));
    await waitFor(() => expect(calls).toContainEqual(["DELETE", "/api/drishti/rules/r_1"]));
  });
});
