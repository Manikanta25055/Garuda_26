import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import PhoneShell from "../src/shells/PhoneShell.svelte";

const source = readFileSync(resolve("src/shells/PhoneShell.svelte"), "utf8");
const props = (over = {}) => ({ current: "house", onchange: () => {}, ...over });

describe("phone shell", () => {
  it("names the first tab for its contents, not as an umbrella", () => {
    render(PhoneShell, props());
    expect(screen.getAllByRole("tab").map((t) => t.textContent.trim()))
      .toEqual(["House", "Rules", "Activity", "Settings"]);
  });

  it("reports a tab change by id", async () => {
    const onchange = vi.fn();
    render(PhoneShell, props({ onchange }));
    await fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(onchange).toHaveBeenCalledWith("activity");
  });

  it("keeps one tab stop and moves within it with the arrows", async () => {
    const onchange = vi.fn();
    render(PhoneShell, props({ current: "house", onchange }));
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1", "-1"]);
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onchange).toHaveBeenCalledWith("rules");
  });

  it("wraps End to the last tab", async () => {
    const onchange = vi.fn();
    render(PhoneShell, props({ onchange }));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    expect(onchange).toHaveBeenCalledWith("settings");
  });

  it("floats clear of the bottom edge and respects the safe area", () => {
    // Asserted against the source: jsdom evaluates neither env() nor the media
    // query, and the claim is about the stylesheet.
    expect(source).toMatch(/env\(safe-area-inset-bottom\)/);
    expect(source).toMatch(/backdrop-filter/);
  });

  it("carries no sidebar rule at any width", () => {
    // The two shells must not overlap. A desktop rule here would mean the
    // laptop design had leaked into the phone one.
    expect(source).not.toMatch(/min-width:\s*768px/);
  });

  it("gives every tab a 44px hit area", () => {
    expect(source).toMatch(/min-height:\s*44px/);
  });

  it("goes solid when the user asks for less transparency", () => {
    expect(source).toMatch(/prefers-reduced-transparency/);
  });

  // ── absorbed from the deleted nav.test.js, which tested the single tab bar ──

  it("wraps around at both ends", async () => {
    const onchange = vi.fn();
    render(PhoneShell, props({ current: "house", onchange }));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(onchange).toHaveBeenCalledWith("settings");
  });

  it("ignores keys that are not navigation", async () => {
    const onchange = vi.fn();
    render(PhoneShell, props({ onchange }));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "a" });
    expect(onchange).not.toHaveBeenCalled();
  });

  it("points every tab at the panel it controls", () => {
    render(PhoneShell, props());
    for (const tab of screen.getAllByRole("tab")) {
      expect(tab).toHaveAttribute("aria-controls", "panel");
    }
  });

  it("tracks the selection by index rather than four separate states", () => {
    const { container } = render(PhoneShell, props({ current: "activity" }));
    expect(container.querySelector(".bar").style.getPropertyValue("--active")).toBe("2");
  });

  it("falls back to the first position for an unknown tab", () => {
    const { container } = render(PhoneShell, props({ current: "nonsense" }));
    expect(container.querySelector(".bar").style.getPropertyValue("--active")).toBe("0");
  });

  it("has one lens, hidden from assistive technology", () => {
    const { container } = render(PhoneShell, props());
    expect(container.querySelectorAll(".lens")).toHaveLength(1);
    expect(container.querySelector(".lens")).toHaveAttribute("aria-hidden", "true");
  });

  it("moves the lens with a spring, and only on transform", () => {
    // Animating width or left would relayout the bar on every tab change.
    expect(source).toMatch(/transition: transform var\(--dur-base\) var\(--ease-spring\)/);
  });

  it("does not rely on colour alone to mark the current tab", () => {
    expect(source).toMatch(/\[aria-selected="true"\] span \{[^}]*font-weight/);
  });

  it("adds no tab stop of its own for the list element", () => {
    // The tablist carries tabindex="-1" so it can hold the key handler for the
    // bubbled events. -1 keeps it out of the tab order; 0 would make crossing
    // the navigation take five presses instead of one.
    const { container } = render(PhoneShell, props());
    expect(container.querySelector('[role="tablist"]')).toHaveAttribute("tabindex", "-1");
  });

  it("builds without an accessibility warning", () => {
    // The two warnings this shell used to emit were linter false positives, but
    // real ones would have been lost in the same noise.
    expect(source).toMatch(/svelte-ignore a11y_no_noninteractive_element_to_interactive_role/);
  });
});
