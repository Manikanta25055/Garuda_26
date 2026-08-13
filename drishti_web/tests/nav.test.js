import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import TabBar from "../src/components/TabBar.svelte";

const source = readFileSync(resolve("src/components/TabBar.svelte"), "utf8");
const appSource = readFileSync(resolve("src/App.svelte"), "utf8");
const composerSource = readFileSync(resolve("src/components/Composer.svelte"), "utf8");

const props = (current = "home", onchange = () => {}) => ({ current, onchange });

describe("the sliding lens", () => {
  it("tracks the selected tab by index rather than by four separate states", () => {
    const { container } = render(TabBar, props("activity"));
    expect(container.querySelector(".bar").style.getPropertyValue("--active")).toBe("2");
  });

  it("starts at the first tab", () => {
    const { container } = render(TabBar, props("home"));
    expect(container.querySelector(".bar").style.getPropertyValue("--active")).toBe("0");
  });

  it("falls back to the first position for an unknown tab", () => {
    const { container } = render(TabBar, props("nonsense"));
    expect(container.querySelector(".bar").style.getPropertyValue("--active")).toBe("0");
  });

  it("is one element, not one per tab", () => {
    const { container } = render(TabBar, props());
    expect(container.querySelectorAll(".lens")).toHaveLength(1);
  });

  it("is hidden from assistive technology", () => {
    const { container } = render(TabBar, props());
    expect(container.querySelector(".lens")).toHaveAttribute("aria-hidden", "true");
  });

  it("moves with a spring, and only transform", () => {
    // Animating width or left would relayout the bar on every tab change.
    expect(source).toMatch(/\.lens \{[^}]*transition: transform var\(--dur-base\) var\(--ease-spring\)/s);
  });

  it("slides horizontally on a phone and vertically on a laptop", () => {
    const phone = source.slice(source.indexOf("max-width: 767.98px"), source.indexOf("min-width: 768px"));
    const laptop = source.slice(source.indexOf("min-width: 768px"));
    expect(phone).toMatch(/translateX\(calc\(var\(--active\)/);
    expect(laptop).toMatch(/translateY\(calc\(var\(--active\)/);
  });

  it("lives inside the tab group so its travel is one tab step", () => {
    // Positioned against the whole bar it would need to know the wordmark's
    // height, and drift the moment that changed.
    expect(source).toMatch(/\.tabs \{ position: relative/);
  });
});

describe("keyboard navigation", () => {
  it("gives the tablist a single tab stop", () => {
    render(TabBar, props("rules"));
    const tabs = screen.getAllByRole("tab");
    expect(tabs.filter((t) => t.getAttribute("tabindex") === "0")).toHaveLength(1);
    expect(screen.getByRole("tab", { name: "Rules" })).toHaveAttribute("tabindex", "0");
  });

  it("moves to the next tab on ArrowRight and ArrowDown", async () => {
    const onchange = vi.fn();
    render(TabBar, props("home", onchange));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onchange).toHaveBeenCalledWith("rules");
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowDown" });
    expect(onchange).toHaveBeenCalledWith("rules");
  });

  it("wraps around at both ends", async () => {
    const onchange = vi.fn();
    render(TabBar, props("home", onchange));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(onchange).toHaveBeenCalledWith("settings");
  });

  it("jumps to the ends with Home and End", async () => {
    const onchange = vi.fn();
    render(TabBar, props("rules", onchange));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    expect(onchange).toHaveBeenCalledWith("settings");
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });
    expect(onchange).toHaveBeenCalledWith("home");
  });

  it("ignores keys that are not navigation", async () => {
    const onchange = vi.fn();
    render(TabBar, props("home", onchange));
    await fireEvent.keyDown(screen.getByRole("tablist"), { key: "a" });
    expect(onchange).not.toHaveBeenCalled();
  });

  it("points each tab at the panel it controls", () => {
    render(TabBar, props("home"));
    for (const tab of screen.getAllByRole("tab")) {
      expect(tab).toHaveAttribute("aria-controls", "panel");
    }
    expect(appSource).toMatch(/id="panel" role="tabpanel"/);
  });
});

describe("responsiveness", () => {
  it("is a bottom bar on a phone and a rail on a laptop", () => {
    expect(source).toMatch(/max-width: 767\.98px[\s\S]*inset: auto 0 0 0/);
    expect(source).toMatch(/min-width: 768px[\s\S]*inset: 0 auto 0 0/);
  });

  it("shows the wordmark only where there is room for it", () => {
    expect(source).toMatch(/\.wordmark \{ display: none/);
    expect(source).toMatch(/min-width: 768px[\s\S]*\.wordmark \{[\s\S]*display: block/);
  });

  it("keeps the rail width and the content offset the same number", () => {
    const rail = source.match(/min-width: 768px[\s\S]*?width: (\d+rem)/)[1];
    expect(appSource).toMatch(new RegExp(`--rail: ${rail}`));
    expect(composerSource).toMatch(new RegExp(`left: ${rail}`));
  });

  it("caps the content column so a wide display does not stretch the text", () => {
    // 100+ character lines are the classic symptom of a phone app widened.
    expect(appSource).toMatch(/max-width: 56rem/);
    expect(composerSource).toMatch(/max-width: 56rem/);
  });

  it("shrinks the title on a laptop, where a phone large-title is oversized", () => {
    expect(appSource).toMatch(/min-width: 768px[\s\S]*--text-title-1/);
  });

  it("still turns the glass solid when transparency is reduced", () => {
    expect(source).toMatch(/prefers-reduced-transparency: reduce[\s\S]*backdrop-filter: none/);
  });
});
