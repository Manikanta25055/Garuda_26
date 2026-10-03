/**
 * Which shell is mounted.
 *
 * 768px is the line the content margin and the type scale already change on, so
 * the shells change there too rather than introducing a second breakpoint.
 *
 * This is a store rather than a CSS breakpoint because the two shells are
 * separate components: only one is ever in the DOM. A phone rule therefore
 * cannot reach a desktop element, and there is nothing to override.
 */
export const DESKTOP_QUERY = "(min-width: 768px)";

export class Viewport {
  isDesktop = $state(false);

  constructor(matchMedia = globalThis.matchMedia) {
    // No throw where matchMedia is absent: this runs at module load, so a
    // failure here would take the app down before anything rendered.
    if (typeof matchMedia !== "function") return;
    const mql = matchMedia(DESKTOP_QUERY);
    this.isDesktop = mql.matches;
    mql.addEventListener("change", (event) => (this.isDesktop = event.matches));
  }
}

export const viewport = new Viewport();
