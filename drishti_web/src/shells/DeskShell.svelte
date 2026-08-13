<script>
  let { current, onchange, children } = $props();

  // Repeated rather than shared with PhoneShell. The two shells are
  // deliberately independent: sharing this would reintroduce exactly the
  // coupling the split exists to remove, and the next change to one would have
  // to be reasoned about in terms of the other.
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

  // The tabs stack, so the arrows that move between them are vertical.
  function onkeydown(event) {
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
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

<nav class="rail" aria-label="Sections" style="--active: {index}">
  <p class="wordmark">Drishti</p>

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

<!-- svelte-ignore a11y_no_noninteractive_element_to_interactive_role -->
<!-- The panel really is the page's main content, and it really is the tabpanel
     the tabs control. The rule reads `tabpanel` as interactive; ARIA does not,
     and splitting the landmark from the panel would leave one of them lying. -->
<main id="panel" role="tabpanel" aria-labelledby="tab-{current}" tabindex="-1">
  <!-- Optional: the shell is a navigation frame in its own right, and is
       rendered without content in its own tests. -->
  {@render children?.()}
</main>

<style>
  /* Content scrolls under the rail's translucency rather than beside an opaque
     panel, which is what makes the material read as glass rather than paint. */
  .rail {
    --tab-h: 44px;
    position: fixed;
    z-index: 20;
    inset: 0 auto 0 0;
    width: var(--rail);
    padding: max(var(--space-8), env(safe-area-inset-top)) var(--space-3) var(--space-4);
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    border-right: 0.5px solid color-mix(in srgb, var(--separator) 60%, transparent);
  }

  .wordmark {
    margin: 0 0 var(--space-5) var(--space-3);
    font-size: var(--text-title-3);
    line-height: var(--lh-title-3);
    font-weight: var(--weight-bold);
    /* Negative tracking as the size grows. */
    letter-spacing: -0.01em;
  }

  .tabs {
    position: relative;
    display: grid;
    grid-auto-rows: var(--tab-h);
    gap: var(--space-1);
  }

  button {
    position: relative;   /* above the lens */
    z-index: 1;
    display: grid;
    grid-auto-flow: column;
    justify-content: start;
    align-items: center;
    gap: var(--space-3);
    min-height: 44px;
    padding: 0 var(--space-3);
    border-radius: var(--radius-control);
    color: var(--label-secondary);
    transition: color var(--dur-base) var(--ease-standard);
  }
  button[aria-selected="true"] { color: var(--accent); }
  button[aria-selected="true"] span { font-weight: var(--weight-semibold); }

  svg { width: 22px; height: 22px; }
  span { font-size: var(--text-callout); font-weight: var(--weight-medium); }

  .lens {
    position: absolute;
    z-index: 0;
    inset: 0 0 auto 0;
    height: var(--tab-h);
    border-radius: var(--radius-control);
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    box-shadow: inset 0 0 0 0.5px color-mix(in srgb, var(--accent) 30%, transparent);
    /* The gap belongs in the step, or the lens drifts off its label. */
    transform: translateY(calc(var(--active) * (var(--tab-h) + var(--space-1))));
    transition: transform var(--dur-base) var(--ease-spring);
    pointer-events: none;
  }

  /* One number governs the rail, the content offset and the composer. Two
     independent values is how content ends up overlapping the rail. */
  main {
    margin-left: var(--rail);
    max-width: var(--measure);
    padding: var(--space-8) var(--space-8) calc(140px + var(--space-4));
  }
  main:focus { outline: none; }

  @media (prefers-reduced-motion: reduce) {
    .lens { transition: none; }
    button { transition: none; }
  }
  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .rail {
      background: var(--surface);
      backdrop-filter: none;
      -webkit-backdrop-filter: none;
    }
  }
</style>
