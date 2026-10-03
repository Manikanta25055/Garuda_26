import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import DeviceTile from "../src/components/DeviceTile.svelte";
import StatusCard from "../src/components/StatusCard.svelte";
import EmptyState from "../src/components/EmptyState.svelte";
import Confirm from "../src/components/Confirm.svelte";
import { phraseFor, isActuator, allOff } from "../src/lib/control.js";

describe("device tile", () => {
  const LAMP = { id: "lamp_desk", name: "Desk lamp", room: "study",
                 type: "light", state: "off", available: true };

  it("names the device and its room", () => {
    render(DeviceTile, { device: LAMP, ontoggle: () => {} });
    expect(screen.getByText("Desk lamp")).toBeInTheDocument();
    expect(screen.getByText(/study/i)).toBeInTheDocument();
  });

  it("states its state in words", () => {
    render(DeviceTile, { device: LAMP, ontoggle: () => {} });
    expect(screen.getByText(/^off$/i)).toBeInTheDocument();
  });

  it("toggles to the opposite state", async () => {
    const ontoggle = vi.fn();
    render(DeviceTile, { device: LAMP, ontoggle });
    await fireEvent.click(screen.getByRole("button"));
    expect(ontoggle).toHaveBeenCalledWith("lamp_desk", "on");
  });

  it("toggles an on device off", async () => {
    const ontoggle = vi.fn();
    render(DeviceTile, { device: { ...LAMP, state: "on" }, ontoggle });
    await fireEvent.click(screen.getByRole("button"));
    expect(ontoggle).toHaveBeenCalledWith("lamp_desk", "off");
  });

  it("marks an unreachable device and refuses to toggle it", async () => {
    const ontoggle = vi.fn();
    render(DeviceTile, { device: { ...LAMP, available: false }, ontoggle });
    expect(screen.getByText(/unreachable/i)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button"));
    expect(ontoggle).not.toHaveBeenCalled();
  });

  it("does not offer to switch a sensor", async () => {
    const ontoggle = vi.fn();
    render(DeviceTile, {
      device: { ...LAMP, type: "sensor.temperature", state: "21" },
      ontoggle,
    });
    await fireEvent.click(screen.getByRole("button"));
    expect(ontoggle).not.toHaveBeenCalled();
  });

  it("has a 44px hit area", () => {
    render(DeviceTile, { device: LAMP, ontoggle: () => {} });
    expect(screen.getByRole("button")).toHaveStyle({ minHeight: "44px" });
  });
});

describe("status card", () => {
  it("answers the question in one line when someone is home", () => {
    render(StatusCard, { state: { occupancy: "occupied", person_count: 2 } });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/someone.s home/i);
    expect(screen.getByText(/2 people in the room/)).toBeInTheDocument();
  });

  it("says one person without a plural", () => {
    render(StatusCard, { state: { occupancy: "occupied", person_count: 1 } });
    expect(screen.getByText(/1 person in the room/)).toBeInTheDocument();
  });

  it("answers it when nobody is home", () => {
    render(StatusCard, { state: { occupancy: "empty", person_count: 0 } });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/nobody.s home/i);
  });

  it("shows no uptime readout", () => {
    // How long the server process has been up is a fact about the server, not
    // about the house. It went with the rest of the developer surface.
    render(StatusCard, { state: { occupancy: "empty", person_count: 0, uptime_s: 7200 } });
    expect(screen.queryByText(/running|uptime|[0-9]+\s*h\b/i)).toBeNull();
  });

  it("survives a state the server has not filled in yet", () => {
    render(StatusCard, { state: {} });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/nobody.s home/i);
  });
});

describe("empty state", () => {
  it("says what to do next rather than that something is missing", () => {
    render(EmptyState, { title: "No devices yet", body: "Add your first device in Settings." });
    expect(screen.getByRole("heading")).toHaveTextContent("No devices yet");
    expect(screen.getByText(/add your first device/i)).toBeInTheDocument();
  });
});

describe("confirm", () => {
  const props = {
    open: true, title: "Turn everything off?",
    body: "Every reachable device switches off.",
    confirmLabel: "Turn off",
  };

  it("requires an explicit confirmation before the destructive action", async () => {
    const onconfirm = vi.fn();
    render(Confirm, { ...props, onconfirm, oncancel: () => {} });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onconfirm).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(onconfirm).toHaveBeenCalledOnce();
  });

  it("cancels without acting", async () => {
    const onconfirm = vi.fn();
    const oncancel = vi.fn();
    render(Confirm, { ...props, onconfirm, oncancel });
    await fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(oncancel).toHaveBeenCalledOnce();
    expect(onconfirm).not.toHaveBeenCalled();
  });

  it("renders nothing when closed", () => {
    render(Confirm, { ...props, open: false, onconfirm: () => {}, oncancel: () => {} });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("control", () => {
  it("phrases an id the local lane can actually match", () => {
    // The lane matches device["id"].replace("_", " ") — the raw id matches
    // nothing at all.
    expect(phraseFor("lamp_desk", "on")).toBe("turn the lamp desk on");
    expect(phraseFor("fan", "off")).toBe("turn the fan off");
  });

  it("knows a sensor is not something to switch", () => {
    expect(isActuator({ type: "light" })).toBe(true);
    expect(isActuator({ type: "sensor.temperature" })).toBe(false);
    expect(isActuator({})).toBe(true);
  });

  it("turns off only reachable actuators that are on", async () => {
    const sent = [];
    vi.stubGlobal("fetch", (url, options) => {
      sent.push(JSON.parse(options.body).text);
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
    });

    const stopped = await allOff([
      { id: "lamp", type: "light", state: "on", available: true },
      { id: "fan", type: "fan", state: "off", available: true },
      { id: "heater", type: "switch", state: "on", available: false },
      { id: "temp", type: "sensor.temperature", state: "21", available: true },
    ]);

    expect(stopped).toEqual(["lamp"]);
    expect(sent).toEqual(["turn the lamp off"]);
  });

  it("reports nothing when everything is already off", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(await allOff([{ id: "lamp", type: "light", state: "off", available: true }])).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
