import { readFileSync, readdirSync, statSync } from "node:fs";
import { resolve, join } from "node:path";
import { describe, it, expect } from "vitest";

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

const sources = walk(resolve("src")).filter((p) => /\.(svelte|css)$/.test(p));
const read = (p) => readFileSync(p, "utf8");

describe("the design system is the only source of values", () => {
  it("states no font weight as a number", () => {
    // A literal 600 is invisible to the scale. The tokens exist so a change to
    // Semibold is one edit rather than a search that misses three files.
    const offenders = sources.filter((p) => /font-weight:\s*\d/.test(read(p)));
    expect(offenders.map((p) => p.replace(resolve("."), ""))).toEqual([]);
  });

  it("measures columns against their container, never the window", () => {
    // The desktop shell puts content in a capped column behind a 15rem rail, so
    // a 1000px window is roughly a 700px column. Every window-width query that
    // laid out cards got the answer wrong.
    const offenders = sources.filter((p) => /@media \(min-width: 900px\)/.test(read(p)));
    expect(offenders.map((p) => p.replace(resolve("."), ""))).toEqual([]);
  });

  it("answers a press immediately, and stops when motion is reduced", () => {
    const base = read(resolve("src/styles/base.css"));
    expect(base).toMatch(/button:active[^{]*\{\s*transform: scale/);
    expect(base).toMatch(/prefers-reduced-motion[\s\S]*button:active[^{]*\{\s*transform: none/);
  });
});
