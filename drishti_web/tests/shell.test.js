import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import TabBar from "../src/components/TabBar.svelte";
import OfflineBanner from "../src/components/OfflineBanner.svelte";

describe("tab bar", () => {
  it("shows exactly the four tabs", () => {
    render(TabBar, { current: "home", onchange: () => {} });
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent.trim())).toEqual(
      ["Home", "Rules", "Activity", "Settings"]);
  });

  it("marks the current tab as selected", () => {
    render(TabBar, { current: "rules", onchange: () => {} });
    expect(screen.getByRole("tab", { name: "Rules" }))
      .toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Home" }))
      .toHaveAttribute("aria-selected", "false");
  });

  it("reports a tab change", async () => {
    const onchange = vi.fn();
    render(TabBar, { current: "home", onchange });
    await fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(onchange).toHaveBeenCalledWith("activity");
  });

  it("has no emergency stop among the navigation targets", () => {
    render(TabBar, { current: "home", onchange: () => {} });
    expect(screen.queryByRole("tab", { name: /stop|emergency/i })).toBeNull();
  });

  it("gives every tab a 44px hit area at both breakpoints", () => {
    // Asserted against the source: jsdom does not evaluate media queries, so
    // a computed-style check would only ever see one of the two branches, and
    // the branch it sees depends on its default window width.
    const src = readFileSync(resolve("src/components/TabBar.svelte"), "utf8");
    const phone = src.slice(src.indexOf("max-width: 767.98px"), src.indexOf("min-width: 768px"));
    const laptop = src.slice(src.indexOf("min-width: 768px"));
    expect(phone).toMatch(/min-height: 44px/);
    expect(laptop).toMatch(/--tab-h: 44px/);
    expect(laptop).toMatch(/height: var\(--tab-h\)/);
  });

  it("hides the glyphs from the accessibility tree", () => {
    const { container } = render(TabBar, { current: "home", onchange: () => {} });
    for (const svg of container.querySelectorAll("svg")) {
      expect(svg).toHaveAttribute("aria-hidden", "true");
    }
  });

  it("does not rely on colour alone to mark the current tab", () => {
    // Asserted against the source: jsdom resolves the 44px rule on the button
    // but not this descendant selector, and the claim is about the stylesheet
    // rather than about jsdom's cascade.
    const source = readFileSync(resolve("src/components/TabBar.svelte"), "utf8");
    expect(source).toMatch(/\[aria-selected="true"\] span \{[^}]*font-weight/);
  });
});

describe("offline banner", () => {
  it("says what still works", () => {
    render(OfflineBanner, { offline: true });
    const alert = screen.getByRole("status");
    expect(alert).toHaveTextContent(/rules are still running/i);
  });

  it("shows nothing when online", () => {
    render(OfflineBanner, { offline: false });
    expect(screen.queryByRole("status")).toBeNull();
  });
});
