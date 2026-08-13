import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import DeskShell from "../src/shells/DeskShell.svelte";

const source = readFileSync(resolve("src/shells/DeskShell.svelte"), "utf8");
const props = (over = {}) => ({ current: "house", onchange: () => {}, ...over });

describe("desk shell", () => {
  it("offers the same four destinations as the phone", () => {
    render(DeskShell, props());
    expect(screen.getAllByRole("tab").map((t) => t.textContent.trim()))
      .toEqual(["House", "Rules", "Activity", "Settings"]);
  });

  it("shows the wordmark the phone has no room for", () => {
    render(DeskShell, props());
    expect(screen.getByText("Drishti")).toBeInTheDocument();
  });

  it("reports a tab change by id", async () => {
    const onchange = vi.fn();
    render(DeskShell, props({ onchange }));
    await fireEvent.click(screen.getByRole("tab", { name: "Rules" }));
    expect(onchange).toHaveBeenCalledWith("rules");
  });

  it("moves with the arrow keys from a single tab stop", async () => {
    const onchange = vi.fn();
    render(DeskShell, props({ current: "house", onchange }));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowDown" });
    expect(onchange).toHaveBeenCalledWith("rules");
  });

  it("carries no bottom-bar rule at any width", () => {
    // The two shells must not overlap. A phone rule here would mean the phone
    // design had leaked into the laptop one.
    expect(source).not.toMatch(/max-width:\s*767/);
    expect(source).not.toMatch(/inset:\s*auto 0 0 0/);
  });

  it("caps the content column so text does not run to a hundred characters", () => {
    expect(source).toMatch(/max-width:\s*var\(--measure\)/);
  });

  it("goes solid when the user asks for less transparency", () => {
    expect(source).toMatch(/prefers-reduced-transparency/);
  });

  // ── absorbed from the deleted nav.test.js ──

  it("keeps one tab stop on the selected tab", () => {
    render(DeskShell, props({ current: "rules" }));
    const tabs = screen.getAllByRole("tab");
    expect(tabs.filter((t) => t.getAttribute("tabindex") === "0")).toHaveLength(1);
    expect(screen.getByRole("tab", { name: "Rules" })).toHaveAttribute("tabindex", "0");
  });

  it("jumps to the ends with Home and End", async () => {
    const onchange = vi.fn();
    render(DeskShell, props({ current: "rules", onchange }));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    expect(onchange).toHaveBeenCalledWith("settings");
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });
    expect(onchange).toHaveBeenCalledWith("house");
  });

  it("points every tab at the panel it controls", () => {
    render(DeskShell, props());
    for (const tab of screen.getAllByRole("tab")) {
      expect(tab).toHaveAttribute("aria-controls", "panel");
    }
  });

  it("slides the lens vertically, since the tabs stack", () => {
    expect(source).toMatch(/translateY\(calc\(var\(--active\)/);
    expect(source).not.toMatch(/translateX\(/);
  });

  it("keeps the rail width and the content offset the same number", () => {
    // Two independent values here is how the content ends up overlapping the
    // rail, or floating away from it.
    expect(source).toMatch(/width: var\(--rail\)/);
    expect(source).toMatch(/margin-left: var\(--rail\)/);
  });

  it("adds no tab stop of its own for the list element", () => {
    const { container } = render(DeskShell, props());
    expect(container.querySelector('[role="tablist"]')).toHaveAttribute("tabindex", "-1");
  });

  it("has one lens, hidden from assistive technology", () => {
    const { container } = render(DeskShell, props());
    expect(container.querySelectorAll(".lens")).toHaveLength(1);
    expect(container.querySelector(".lens")).toHaveAttribute("aria-hidden", "true");
  });
});
