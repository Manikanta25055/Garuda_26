import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import Activity from "../src/routes/Activity.svelte";

function stub(entries) {
  vi.stubGlobal("fetch", () =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ entries }) }));
}

describe("activity screen", () => {
  it("names the device, action and the conditions that matched", async () => {
    stub([{ ts: Date.now() / 1000, device: "fan", action: "off", rule_id: "r_1",
            matched: [{ field: "occupancy", op: "==", value: "empty" }], ok: true, reason: "" }]);

    render(Activity);

    await waitFor(() => expect(screen.getByText(/fan/)).toBeInTheDocument());
    expect(screen.getByText(/occupancy == empty/)).toBeInTheDocument();
  });

  it("joins several matched conditions", async () => {
    stub([{ ts: Date.now() / 1000, device: "fan", action: "off", rule_id: "r_1",
            matched: [{ field: "occupancy", op: "==", value: "empty" },
                      { field: "hour", op: ">=", value: 22 }], ok: true, reason: "" }]);

    render(Activity);

    await waitFor(() =>
      expect(screen.getByText("occupancy == empty and hour >= 22")).toBeInTheDocument());
  });

  it("marks a failed actuation with its reason", async () => {
    stub([{ ts: Date.now() / 1000, device: "heater", action: "on", rule_id: "r_2",
            matched: [], ok: false, reason: "device 'heater' is unreachable" }]);

    render(Activity);

    await waitFor(() => expect(screen.getByText(/unreachable/)).toBeInTheDocument());
    expect(screen.getByText(/didn't work/i)).toBeInTheDocument();
  });

  it("survives an entry the server wrote without a matched list", async () => {
    stub([{ ts: Date.now() / 1000, device: "fan", action: "off", rule_id: null, ok: true }]);

    render(Activity);

    await waitFor(() => expect(screen.getByText(/fan/)).toBeInTheDocument());
  });

  it("says nothing has happened yet rather than showing an empty list", async () => {
    stub([]);
    render(Activity);
    await waitFor(() => expect(screen.getByText(/nothing has happened yet/i)).toBeInTheDocument());
  });
});
