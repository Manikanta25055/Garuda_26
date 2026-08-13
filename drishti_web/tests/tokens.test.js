import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

// Not import.meta.url: under the jsdom environment that resolves to an http
// URL, and readFileSync only takes file: ones. vitest runs from the package
// root, so a relative path is stable.
const tokens = readFileSync(resolve("src/styles/tokens.css"), "utf8");

describe("design tokens", () => {
  it("defines the iOS type scale in rem so user text size scales it", () => {
    expect(tokens).toMatch(/--text-body:\s*1\.0625rem/);
    expect(tokens).toMatch(/--text-large-title:\s*2\.125rem/);
    expect(tokens).toMatch(/--text-caption-2:\s*0\.6875rem/);
    expect(tokens).not.toMatch(/--text-[a-z0-9-]+:\s*\d+px/);
  });

  it("uses the system font stack rather than a downloaded face", () => {
    expect(tokens).toMatch(/-apple-system/);
    expect(tokens).toMatch(/system-ui/);
  });

  it("defines light and dark semantic colours", () => {
    expect(tokens).toMatch(/prefers-color-scheme:\s*dark/);
    expect(tokens).toMatch(/--label-secondary/);
    expect(tokens).toMatch(/--separator/);
  });

  it("lets an explicit theme attribute beat the OS in both directions", () => {
    expect(tokens).toMatch(/\[data-theme="dark"\]/);
    expect(tokens).toMatch(/\[data-theme="light"\]/);
  });

  it("honours reduced motion, contrast and transparency", () => {
    expect(tokens).toMatch(/prefers-reduced-motion:\s*reduce/);
    expect(tokens).toMatch(/prefers-contrast:\s*more/);
    expect(tokens).toMatch(/prefers-reduced-transparency:\s*reduce/);
  });

  it("removes backdrop-filter when transparency is reduced", () => {
    const block = tokens.slice(tokens.indexOf("prefers-reduced-transparency"));
    expect(block).toMatch(/backdrop-filter:\s*none/);
  });

  it("never falls back to a Light or Thin weight", () => {
    expect(tokens).not.toMatch(/font-weight:\s*(100|200|300)\b/);
    expect(tokens).toMatch(/--weight-regular:\s*400/);
  });

  it("keeps the dark override from swallowing an explicit light choice", () => {
    // A bare :root inside the dark media query would beat :root[data-theme="light"]
    // on specificity for nobody, but win the cascade by source order for
    // everyone whose OS is dark. The :not() is what stops that.
    const dark = tokens.slice(tokens.indexOf("prefers-color-scheme: dark"));
    expect(dark.slice(0, 120)).toMatch(/:root:not\(\[data-theme="light"\]\)/);
  });

  it("states a minimum hit area", () => {
    expect(tokens).toMatch(/--hit-min:\s*44px/);
  });
});
