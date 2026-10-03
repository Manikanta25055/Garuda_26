import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import LiveView from "../src/components/LiveView.svelte";

const source = readFileSync(resolve("src/components/LiveView.svelte"), "utf8");
const props = (over = {}) => ({ privacy: false, onprivacy: () => {}, ...over });

describe("live view", () => {
  it("shows the camera when privacy is off", () => {
    render(LiveView, props());
    expect(screen.getByAltText(/live camera/i)).toBeInTheDocument();
  });

  it("shows no camera element at all when privacy is on", () => {
    // Not merely hidden: an img left in the DOM keeps pulling the stream, so
    // "off" would still be holding the connection open.
    render(LiveView, props({ privacy: true }));
    expect(screen.queryByAltText(/live camera/i)).toBeNull();
  });

  it("says the camera is off rather than looking broken", () => {
    render(LiveView, props({ privacy: true }));
    expect(screen.getByText(/camera is off/i)).toBeInTheDocument();
  });

  it("offers a switch that reports the state being asked for", async () => {
    const onprivacy = vi.fn();
    render(LiveView, props({ privacy: false, onprivacy }));
    await fireEvent.click(screen.getByRole("switch", { name: /camera/i }));
    expect(onprivacy).toHaveBeenCalledWith(true);
  });

  it("asks to turn the camera back on when it is already off", async () => {
    const onprivacy = vi.fn();
    render(LiveView, props({ privacy: true, onprivacy }));
    await fireEvent.click(screen.getByRole("switch", { name: /camera/i }));
    expect(onprivacy).toHaveBeenCalledWith(false);
  });

  it("reports the switch state to assistive technology", () => {
    render(LiveView, props({ privacy: true }));
    expect(screen.getByRole("switch", { name: /camera/i }))
      .toHaveAttribute("aria-checked", "true");
  });

  it("holds a fixed shape so the page does not jump when a frame arrives", () => {
    expect(source).toMatch(/aspect-ratio/);
    expect(source).toMatch(/object-fit:\s*cover/);
  });

  it("stacks its layers, so an overlay can be added without moving anything", () => {
    expect(source).toMatch(/position:\s*relative/);
  });

  it("gives the switch a 44px hit area", () => {
    expect(source).toMatch(/min-height:\s*44px/);
  });

  it("goes solid when the user asks for less transparency", () => {
    expect(source).toMatch(/prefers-reduced-transparency/);
  });
});
