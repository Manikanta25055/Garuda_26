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
</script>

<div role="tablist" aria-label="Sections">
  {#each TABS as tab}
    <button
      role="tab"
      aria-selected={current === tab.id}
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

<style>
  /* Control layer: floats above content, which scrolls beneath it. */
  [role="tablist"] {
    position: fixed;
    inset: auto 0 0 0;
    z-index: 20;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    padding: var(--space-1) var(--space-2);
    padding-bottom: max(var(--space-1), env(safe-area-inset-bottom));
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    border-top: 0.5px solid color-mix(in srgb, var(--separator) 60%, transparent);
  }
  button {
    min-height: 44px;
    display: grid;
    justify-items: center;
    gap: 2px;
    padding: var(--space-1) 0;
    color: var(--label-secondary);
    transition: color var(--dur-fast) var(--ease-standard);
  }
  /* The selected tab is not distinguished by colour alone: its label carries
   * aria-selected, and weight changes with it. */
  button[aria-selected="true"] { color: var(--accent); }
  button[aria-selected="true"] span { font-weight: 600; }
  svg { width: 24px; height: 24px; }
  span { font-size: var(--text-caption-2); line-height: var(--lh-caption-2); font-weight: 500; }

  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    [role="tablist"] { backdrop-filter: none; -webkit-backdrop-filter: none; background: var(--surface); }
  }
</style>
