<script>
  let { device, ontoggle, busy = false } = $props();
  const next = $derived(device.state === "on" ? "off" : "on");
  const actuator = $derived(!device.type.startsWith("sensor."));
  const usable = $derived(device.available && actuator && !busy);
</script>

<button
  onclick={() => usable && ontoggle(device.id, next)}
  aria-disabled={!usable}
  aria-busy={busy}
  class:on={device.state === "on"}
  class:busy
>
  <span class="name">{device.name}</span>
  <span class="room">{device.room}</span>
  {#if busy}
    <span class="state">Switching…</span>
  {:else if device.available}
    <span class="state">{device.state}</span>
  {:else}
    <span class="state warn">Unreachable</span>
  {/if}
</button>

<style>
  button {
    min-height: 44px;
    display: grid;
    gap: 2px;
    padding: var(--space-3);
    text-align: left;
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-radius: var(--radius-card);
    transition: background var(--dur-fast) var(--ease-standard),
                transform var(--dur-fast) var(--ease-standard);
  }
  /* A press that moves is a press that registered. */
  button:active:not([aria-disabled="true"]) { transform: scale(0.97); }
  button.on { background: color-mix(in srgb, var(--accent) 14%, var(--surface)); }
  button[aria-disabled="true"] { opacity: 0.55; cursor: default; }
  button.busy { opacity: 0.75; }
  .name { font-size: var(--text-callout); font-weight: var(--weight-semibold); }
  .room, .state { font-size: var(--text-caption-1); color: var(--label-secondary); text-transform: capitalize; }
  .warn { color: var(--warning); }
</style>
