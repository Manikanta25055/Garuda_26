import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import DeviceTile from "../src/components/DeviceTile.svelte";

const read = (p) => readFileSync(resolve(p), "utf8");

const LAMP = { id: "lamp", name: "Lamp", room: "study", type: "light",
               state: "off", available: true };

describe("device tile feedback", () => {
  it("says it is switching while the request is in flight", () => {
    render(DeviceTile, { device: LAMP, ontoggle: () => {}, busy: true });
    expect(screen.getByText(/switching/i)).toBeInTheDocument();
  });

  it("marks itself busy for assistive technology", () => {
    render(DeviceTile, { device: LAMP, ontoggle: () => {}, busy: true });
    expect(screen.getByRole("button")).toHaveAttribute("aria-busy", "true");
  });

  it("refuses a second tap while the first is still running", async () => {
    const ontoggle = vi.fn();
    render(DeviceTile, { device: LAMP, ontoggle, busy: true });
    await fireEvent.click(screen.getByRole("button"));
    expect(ontoggle).not.toHaveBeenCalled();
  });

  it("is tappable again once the request lands", async () => {
    const ontoggle = vi.fn();
    render(DeviceTile, { device: LAMP, ontoggle, busy: false });
    await fireEvent.click(screen.getByRole("button"));
    expect(ontoggle).toHaveBeenCalledWith("lamp", "on");
  });
});

describe("loading", () => {
  it("House holds card-shaped space instead of spinning", () => {
    // A spinner reflows the page when the real content lands; a skeleton the
    // size of the thing it replaces does not.
    const house = read("src/routes/House.svelte");
    expect(house).toMatch(/class="skeleton tile-skeleton"/);
    expect(house).toMatch(/\.tile-skeleton \{ height/);
  });

  it("announces the load to a screen reader rather than only drawing it", () => {
    expect(read("src/routes/House.svelte")).toMatch(/Loading your devices/);
  });

  it("stops the skeleton shimmer under reduced motion", () => {
    expect(read("src/styles/base.css"))
      .toMatch(/prefers-reduced-motion: reduce\s*\)\s*\{\s*\.skeleton \{ animation: none/);
  });
});

describe("desktop layout", () => {
  it("Rules and Activity column up against their own column, not the window", () => {
    // On the desktop shell the content column is the window minus a 15rem rail
    // and its padding, capped at the measure -- so a 1000px window is a 700px
    // column, and a window-width query gets the answer wrong.
    for (const file of ["src/routes/Rules.svelte", "src/routes/Activity.svelte"]) {
      const src = read(file);
      expect(src).toMatch(/@container panel \(min-width: 44rem\)[\s\S]*repeat\(auto-fill, minmax\(/);
      expect(src).not.toMatch(/@media \(min-width: 900px\)/);
    }
  });

  it("both shells declare the panel as the container those decks query", () => {
    for (const shell of ["src/shells/PhoneShell.svelte", "src/shells/DeskShell.svelte"]) {
      expect(read(shell)).toMatch(/container-name: panel/);
    }
  });

  it("Settings stays a single column, because its sections are read in order", () => {
    // Cards side by side would say these are alternatives to choose between.
    // They are not: they are a list of things about the house, and the desktop
    // shell already caps the column so it cannot stretch.
    expect(read("src/routes/Settings.svelte")).not.toMatch(/repeat\(auto-fill/);
  });

  it("sizes columns by the card's comfortable width, not by a fixed count", () => {
    // A fixed column count leaves a gap at every width it was not chosen for.
    expect(read("src/routes/Rules.svelte")).not.toMatch(/grid-template-columns: repeat\([0-9]/);
  });

  it("Login becomes a centred card on a laptop", () => {
    expect(read("src/routes/Login.svelte")).toMatch(/min-width: 768px[\s\S]*box-shadow/);
  });
});
