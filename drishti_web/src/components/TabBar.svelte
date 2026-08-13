<script>
  let { current, onchange } = $props();

  // Four tabs for every role. Settings gates its contents by role rather than
  // the tab disappearing, so the app does not change shape depending on who is
  // signed in.
  const TABS = [
    { id: "home", label: "Home",
      d: ["M3 11l9-8 9 8v9a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z"] },
    { id: "rules", label: "Rules",
      d: ["M4 6h16M4 12h16M4 18h10"] },
    { id: "activity", label: "Activity",
      d: ["M3 12h4l3 8 4-16 3 8h4"] },
    { id: "settings", label: "Settings",
      d: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
          "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"] },
  ];

  const index = $derived(Math.max(0, TABS.findIndex((t) => t.id === current)));

  // A tablist owns one tab stop; the arrows move within it. Without this a
  // keyboard user presses Tab four times to cross the navigation.
  function onkeydown(event) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
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

<nav class="bar" aria-label="Sections" style="--count: {TABS.length}; --active: {index}">
  <p class="wordmark" aria-hidden="true">Drishti</p>

  <div class="tabs" role="tablist" {onkeydown}>
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
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          {#each tab.d as path}<path d={path} />{/each}
        </svg>
        <span>{tab.label}</span>
      </button>
    {/each}
  </div>
</nav>

<style>
  /* Control layer: floats above content, which scrolls beneath it. */
  .bar {
    position: fixed;
    z-index: 20;
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
  }

  /* The lens lives inside the tab group, so its travel is one tab step and
     needs no knowledge of what sits above it. */
  .tabs { position: relative; display: grid; }

  button {
    position: relative;   /* above the lens */
    z-index: 1;
    display: grid;
    color: var(--label-secondary);
    transition: color var(--dur-base) var(--ease-standard);
  }
  button[aria-selected="true"] { color: var(--accent); }
  /* The label changes weight as well as colour, so the selection survives
     colour-vision deficiency. */
  button[aria-selected="true"] span { font-weight: 600; }
  svg { width: 24px; height: 24px; }

  /* One capsule that slides between tabs, rather than four states appearing
     and disappearing. The movement is what says where you came from, so it is
     a spring — and it is the only glass in the app that moves. */
  .lens {
    position: absolute;
    z-index: 0;
    inset: 0 auto 0 0;
    border-radius: 9999px;
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    box-shadow: inset 0 0 0 0.5px color-mix(in srgb, var(--accent) 30%, transparent);
    transition: transform var(--dur-base) var(--ease-spring);
    pointer-events: none;
  }

  .wordmark { display: none; }

  /* ── Phone: the bar sits at the bottom, under the thumb ─────────────────── */
  @media (max-width: 767.98px) {
    .bar {
      inset: auto 0 0 0;
      padding: var(--space-1) var(--space-2);
      padding-bottom: max(var(--space-1), env(safe-area-inset-bottom));
      border-top: 0.5px solid color-mix(in srgb, var(--separator) 60%, transparent);
    }
    .tabs { grid-template-columns: repeat(var(--count), 1fr); }
    button {
      min-height: 44px;
      justify-items: center;
      gap: 2px;
      padding: var(--space-1) 0;
    }
    span { font-size: var(--text-caption-2); line-height: var(--lh-caption-2); font-weight: 500; }
    .lens {
      width: calc(100% / var(--count));
      transform: translateX(calc(var(--active) * 100%));
    }
  }

  /* ── Laptop: the bar becomes a rail. Four icons stranded at the bottom of a
        1440px window is a phone app in a big frame, not a desktop app ─────── */
  @media (min-width: 768px) {
    .bar {
      --tab-h: 44px;
      inset: 0 auto 0 0;
      width: 15rem;
      padding: max(var(--space-6), env(safe-area-inset-top)) var(--space-3) var(--space-4);
      border-right: 0.5px solid color-mix(in srgb, var(--separator) 60%, transparent);
    }
    .wordmark {
      display: block;
      margin: 0 0 var(--space-5) var(--space-3);
      font-size: var(--text-title-3);
      line-height: var(--lh-title-3);
      font-weight: 700;
      letter-spacing: -0.01em;
    }
    .tabs { grid-auto-rows: var(--tab-h); gap: var(--space-1); }
    button {
      grid-auto-flow: column;
      justify-content: start;
      align-items: center;
      gap: var(--space-3);
      padding: 0 var(--space-3);
      border-radius: var(--radius-control);
    }
    span { font-size: var(--text-callout); font-weight: 500; }
    .lens {
      inset: 0 0 auto 0;
      height: var(--tab-h);
      border-radius: var(--radius-control);
      /* The gap belongs in the step, or the lens drifts off its label. */
      transform: translateY(calc(var(--active) * (var(--tab-h) + var(--space-1))));
    }
  }

  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .bar { backdrop-filter: none; -webkit-backdrop-filter: none; background: var(--surface); }
  }
</style>
