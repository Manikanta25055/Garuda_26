import { describe, it, expect } from "vitest";
import { Viewport, DESKTOP_QUERY } from "../src/lib/viewport.svelte.js";

function fakeMatchMedia(matches) {
  const listeners = [];
  const mql = {
    matches,
    media: DESKTOP_QUERY,
    addEventListener: (_, fn) => listeners.push(fn),
    removeEventListener: () => {},
  };
  const fn = (query) => {
    fn.lastQuery = query;
    return mql;
  };
  fn.fire = (next) => {
    mql.matches = next;
    listeners.forEach((l) => l({ matches: next }));
  };
  return fn;
}

describe("viewport", () => {
  it("asks about the 768px line the tokens already change on", () => {
    const mm = fakeMatchMedia(false);
    new Viewport(mm);
    expect(mm.lastQuery).toBe("(min-width: 768px)");
  });

  it("starts on the phone shell below the line", () => {
    expect(new Viewport(fakeMatchMedia(false)).isDesktop).toBe(false);
  });

  it("starts on the desktop shell above the line", () => {
    expect(new Viewport(fakeMatchMedia(true)).isDesktop).toBe(true);
  });

  it("follows the window across the line", () => {
    const mm = fakeMatchMedia(false);
    const v = new Viewport(mm);
    mm.fire(true);
    expect(v.isDesktop).toBe(true);
    mm.fire(false);
    expect(v.isDesktop).toBe(false);
  });

  it("stays on the phone shell where matchMedia does not exist", () => {
    // Not decoration: jsdom has historically shipped without it, and a throw
    // here would take the whole app down before anything rendered.
    expect(new Viewport(undefined).isDesktop).toBe(false);
  });
});
