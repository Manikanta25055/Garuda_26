import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import DeviceTile from "../src/components/DeviceTile.svelte";
import Settings from "../src/routes/Settings.svelte";
import { session } from "../src/lib/session.svelte.js";
import { house } from "../src/lib/app.svelte.js";

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
  it("Home holds card-shaped space instead of spinning", () => {
    // A spinner reflows the page when the real content lands; a skeleton the
    // size of the thing it replaces does not.
    const home = read("src/routes/Home.svelte");
    expect(home).toMatch(/class="skeleton tile-skeleton"/);
    expect(home).toMatch(/\.tile-skeleton \{ height/);
  });

  it("announces the load to a screen reader rather than only drawing it", () => {
    expect(read("src/routes/Home.svelte")).toMatch(/Loading your devices/);
  });

  it("stops the skeleton shimmer under reduced motion", () => {
    expect(read("src/styles/base.css"))
      .toMatch(/prefers-reduced-motion: reduce\s*\)\s*\{\s*\.skeleton \{ animation: none/);
  });
});

describe("desktop layout", () => {
  it("Home puts status and camera side by side when there is room", () => {
    expect(read("src/routes/Home.svelte"))
      .toMatch(/min-width: 900px[\s\S]*\.top \{ grid-template-columns/);
  });

  it("Rules, Activity and Settings column up rather than running one long strip", () => {
    for (const file of ["src/routes/Rules.svelte", "src/routes/Activity.svelte",
                        "src/routes/Settings.svelte"]) {
      expect(read(file)).toMatch(/min-width: 900px[\s\S]*repeat\(auto-fill, minmax\(/);
    }
  });

  it("sizes columns by the card's comfortable width, not by a fixed count", () => {
    // A fixed column count leaves a gap at every width it was not chosen for.
    expect(read("src/routes/Rules.svelte")).not.toMatch(/grid-template-columns: repeat\([0-9]/);
  });

  it("Login becomes a centred card on a laptop", () => {
    expect(read("src/routes/Login.svelte")).toMatch(/min-width: 768px[\s\S]*box-shadow/);
  });
});

describe("settings shows what the rule loop is doing", () => {
  it("reports the loop, its counts and the camera", async () => {
    session.username = "mani";
    session.role = "admin";
    house.state = {
      pipeline: "running",
      rule_loop: { running: true, ticks: 42, fires: 3, rules: 2,
                   orphaned_rules: 0, last_error: "" },
    };
    vi.stubGlobal("fetch", () => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ devices: [], rule_loop: house.state.rule_loop,
                                    pipeline: "running" }),
    }));

    const { container } = render(Settings);

    // Read each term's own definition. Both the loop and the camera say
    // "running", so a bare text query matches two nodes and throws.
    const definitionFor = (term) => {
      const dt = [...container.querySelectorAll("dt")]
        .find((el) => el.textContent.trim() === term);
      return dt?.nextElementSibling?.textContent.trim();
    };

    await waitFor(() => expect(definitionFor("Rule loop")).toBe("running"));
    expect(definitionFor("Evaluated")).toBe("42 times");
    expect(definitionFor("Actions taken")).toBe("3");
    expect(definitionFor("Rules")).toBe("2");
    expect(definitionFor("Camera")).toBe("running");
  });

  it("surfaces the error the loop swallowed instead of looking idle", async () => {
    session.username = "mani";
    session.role = "admin";
    house.state = {
      pipeline: "stopped",
      rule_loop: { running: true, ticks: 9, fires: 0, rules: 1,
                   orphaned_rules: 0, last_error: "KeyError: 'occupancy'" },
    };
    vi.stubGlobal("fetch", () => Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve({ devices: [] }),
    }));

    render(Settings);

    expect(await screen.findByRole("alert")).toHaveTextContent(/KeyError/);
  });
});
