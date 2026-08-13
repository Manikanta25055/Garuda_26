<script>
  let { current, onchange, children } = $props();

  // Named for their contents. "Home" was an umbrella that told you nothing
  // about what was behind it.
  const TABS = [
    { id: "house", label: "House",
      d: ["M3 11l9-8 9 8v9a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z"] },
    { id: "rules", label: "Rules", d: ["M4 6h16M4 12h16M4 18h10"] },
    { id: "activity", label: "Activity", d: ["M3 12h4l3 8 4-16 3 8h4"] },
    { id: "settings", label: "Settings",
      d: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
          "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"] },
  ];

  const index = $derived(Math.max(0, TABS.findIndex((t) => t.id === current)));

  // A tablist owns one tab stop; the arrows move within it. Without this a
  // keyboard user presses Tab four times to cross the navigation.
  function onkeydown(event) {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (step) {
      event.preventDefault();
      onchange(TABS[(index + step + TABS.length) % TABS.length].id);
    } else if (event.key === "Home") {
      event.preventDefault();
      onchange(TABS[0].id);
    } else if (event.key === "End") {
      event.preventDefault();
      onchange(TABS[TABS.length - 1].id);
    }
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_to_interactive_role -->
<!-- The panel really is the page's main content, and it really is the tabpanel
     the tabs control. The rule reads `tabpanel` as interactive; ARIA does not,
     and splitting the landmark from the panel would leave one of them lying. -->
<main id="panel" role="tabpanel" aria-labelledby="tab-{current}" tabindex="-1">
  <!-- Optional: the shell is a navigation frame in its own right, and is
       rendered without content in its own tests. -->
  {@render children?.()}
</main>

<nav class="bar" aria-label="Sections" style="--count: {TABS.length}; --active: {index}">
  <!-- tabindex="-1" on the list itself, not 0: it takes the bubbled key events
       from whichever tab has focus, so it must be focusable to carry the
       handler, but it must never become a fifth stop in the tab order. -->
  <div class="tabs" role="tablist" tabindex="-1" {onkeydown}>
    <span class="lens" aria-hidden="true"></span>

    {#each TABS as tab, i}
      <button
        role="tab"
        id="tab-{tab.id}"
        aria-selected={current === tab.id}
        aria-controls="panel"
        tabindex={i === index ? 0 : -1}
        onclick={() => onchange(tab.id)}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          {#each tab.d as path}<path d={path} />{/each}
        </svg>
        <span>{tab.label}</span>
      </button>
    {/each}
  </div>
</nav>

<style>
  main {
    padding: max(var(--space-4), env(safe-area-inset-top)) var(--margin-content) 0;
    /* Clears the composer and the floating bar beneath it. */
    /* Clears the composer, which itself clears the capsule. */
    padding-bottom: calc(var(--dock-clear) + 76px + env(safe-area-inset-bottom));
  }
  main:focus { outline: none; }

  /* A capsule that floats clear of the edge, rather than a strip welded to the
     chrome. The inset is what makes it read as an object above the content. */
  .bar {
    position: fixed;
    z-index: 20;
    inset: auto var(--space-3) calc(var(--bar-inset-b) + env(safe-area-inset-bottom)) var(--space-3);
    padding: var(--space-1);
    border-radius: 9999px;
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    box-shadow: 0 8px 32px rgb(0 0 0 / 0.16);
    /* A bright top edge reads as light catching a real material. */
    border-top: 0.5px solid rgb(255 255 255 / 0.4);
  }

  .tabs {
    position: relative;
    display: grid;
    grid-template-columns: repeat(var(--count), 1fr);
  }

  button {
    position: relative;   /* above the lens */
    z-index: 1;
    display: grid;
    justify-items: center;
    gap: 2px;
    min-height: 44px;
    padding: var(--space-1) 0;
    color: var(--label-secondary);
    transition: color var(--dur-base) var(--ease-standard);
  }
  button[aria-selected="true"] { color: var(--accent); }
  /* Weight as well as colour, so the selection survives colour-vision
     deficiency. */
  button[aria-selected="true"] span { font-weight: var(--weight-semibold); }

  svg { width: 24px; height: 24px; }
  span {
    font-size: var(--text-caption-2);
    line-height: var(--lh-caption-2);
    font-weight: var(--weight-medium);
  }

  /* One capsule that slides between tabs, rather than four states appearing and
     disappearing. transform only, so a tab change never relayouts the bar. */
  .lens {
    position: absolute;
    z-index: 0;
    inset: 0 auto 0 0;
    width: calc(100% / var(--count));
    border-radius: 9999px;
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    box-shadow: inset 0 0 0 0.5px color-mix(in srgb, var(--accent) 30%, transparent);
    transform: translateX(calc(var(--active) * 100%));
    transition: transform var(--dur-base) var(--ease-spring);
    pointer-events: none;
  }

  @media (prefers-reduced-motion: reduce) {
    .lens { transition: none; }
    button { transition: none; }
  }
  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .bar {
      background: var(--surface);
      backdrop-filter: none;
      -webkit-backdrop-filter: none;
      border: 0.5px solid var(--separator);
    }
  }
</style>
