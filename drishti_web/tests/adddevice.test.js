import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import AddDevice from "../src/routes/AddDevice.svelte";

const TYPES = {
  light: { actions: ["off", "on"], state: { kind: "enum", values: ["on", "off"] } },
  "sensor.temperature": { actions: [], state: { kind: "num", lo: -10, hi: 60 } },
};

function stubRoutes(overrides = {}) {
  vi.stubGlobal("fetch", (path, options) => {
    if (path === "/api/drishti/device-types") {
      return Promise.resolve({ ok: true, status: 200,
        json: () => Promise.resolve({ types: TYPES, channels: overrides.channels ?? [1, 2, 3] }) });
    }
    if (overrides.postFails) {
      return Promise.resolve({ ok: false, status: 400,
        json: () => Promise.resolve({ detail: "channel 3 is already in use" }) });
    }
    overrides.captured?.push(JSON.parse(options.body));
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
  });
}

async function ready() {
  await waitFor(() => expect(screen.getByLabelText(/type/i)).toBeInTheDocument());
}

describe("add device", () => {
  it("offers only catalogue types", async () => {
    stubRoutes();
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();
    const options = [...screen.getByLabelText(/type/i).options].map((o) => o.value);
    expect(options).toEqual(["light", "sensor.temperature"]);
  });

  it("never asks for a GPIO pin", async () => {
    stubRoutes();
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();
    expect(screen.queryByLabelText(/pin|bcm|gpio/i)).toBeNull();
    expect(screen.getByLabelText(/channel/i)).toBeInTheDocument();
  });

  it("offers the channels the server reports, not a hardcoded list", async () => {
    stubRoutes({ channels: [4, 5] });
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();
    await waitFor(() =>
      expect([...screen.getByLabelText(/channel/i).options].map((o) => o.value))
        .toEqual(["4", "5"]));
  });

  it("submits the four fields and derives nothing else", async () => {
    const captured = [];
    stubRoutes({ captured });
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();

    await fireEvent.change(screen.getByLabelText(/type/i), { target: { value: "light" } });
    await fireEvent.input(screen.getByLabelText(/name/i), { target: { value: "Desk lamp" } });
    await fireEvent.input(screen.getByLabelText(/room/i), { target: { value: "study" } });
    await fireEvent.click(screen.getByRole("button", { name: /add device/i }));

    await waitFor(() => expect(captured.length).toBe(1));
    expect(captured[0]).toMatchObject({ name: "Desk lamp", type: "light", room: "study" });
    expect(captured[0].transport.kind).toBe("relay");
    expect(captured[0]).not.toHaveProperty("actions");
  });

  it("derives an id the server's own pattern accepts", async () => {
    const captured = [];
    stubRoutes({ captured });
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();

    await fireEvent.input(screen.getByLabelText(/name/i), { target: { value: "  Desk Lamp #2 " } });
    await fireEvent.click(screen.getByRole("button", { name: /add device/i }));

    await waitFor(() => expect(captured.length).toBe(1));
    expect(captured[0].id).toBe("desk_lamp_2");
    expect(captured[0].id).toMatch(/^[a-z][a-z0-9_]{1,31}$/);
  });

  it("says so itself when the name is too short to make an id", async () => {
    const captured = [];
    stubRoutes({ captured });
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();

    await fireEvent.input(screen.getByLabelText(/name/i), { target: { value: "A" } });
    await fireEvent.click(screen.getByRole("button", { name: /add device/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/longer name/i);
    expect(captured).toHaveLength(0);
  });

  it("shows the server's refusal verbatim", async () => {
    stubRoutes({ postFails: true });
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();

    await fireEvent.input(screen.getByLabelText(/name/i), { target: { value: "Lamp" } });
    await fireEvent.click(screen.getByRole("button", { name: /add device/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("channel 3 is already in use");
  });

  it("asks for a topic instead of a channel for an MQTT device", async () => {
    stubRoutes();
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();

    await fireEvent.change(screen.getByLabelText(/connection/i), { target: { value: "mqtt" } });

    expect(screen.getByLabelText(/topic/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/channel/i)).toBeNull();
  });

  it("sends the topic for an MQTT device", async () => {
    const captured = [];
    stubRoutes({ captured });
    render(AddDevice, { onadded: () => {}, oncancel: () => {} });
    await ready();

    await fireEvent.change(screen.getByLabelText(/connection/i), { target: { value: "mqtt" } });
    await fireEvent.input(screen.getByLabelText(/name/i), { target: { value: "Heater" } });
    await fireEvent.input(screen.getByLabelText(/topic/i), { target: { value: "drishti/heater" } });
    await fireEvent.click(screen.getByRole("button", { name: /add device/i }));

    await waitFor(() => expect(captured.length).toBe(1));
    expect(captured[0].transport).toEqual({ kind: "mqtt", topic_base: "drishti/heater" });
  });

  it("cancels without adding anything", async () => {
    const oncancel = vi.fn();
    const captured = [];
    stubRoutes({ captured });
    render(AddDevice, { onadded: () => {}, oncancel });
    await ready();

    await fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(oncancel).toHaveBeenCalledOnce();
    expect(captured).toHaveLength(0);
  });
});
