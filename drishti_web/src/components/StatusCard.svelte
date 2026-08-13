<script>
  let { state } = $props();
  const hours = $derived(Math.floor((state.uptime_s ?? 0) / 3600));
</script>

<section>
  <p class="headline">
    {state.occupancy === "occupied" ? "Someone's home" : "Nobody's home"}
  </p>
  <p class="detail">
    {state.occupancy === "occupied"
      ? `${state.person_count} in the room`
      : "The room is empty"}
  </p>
  <p class="meta">Running {hours} h</p>
</section>

<style>
  section {
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-radius: var(--radius-card);
    padding: var(--space-4);
    display: grid;
    gap: var(--space-1);
  }
  .headline { margin: 0; font-size: var(--text-title-2); line-height: var(--lh-title-2); font-weight: 600; }
  .detail { margin: 0; color: var(--label-secondary); }
  .meta { margin: 0; font-size: var(--text-caption-1); color: var(--label-tertiary); }
</style>
