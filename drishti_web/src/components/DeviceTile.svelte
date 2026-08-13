<script>
  let { device, ontoggle } = $props();
  const next = $derived(device.state === "on" ? "off" : "on");
  const actuator = $derived(!device.type.startsWith("sensor."));
</script>

<button
  onclick={() => device.available && actuator && ontoggle(device.id, next)}
  aria-disabled={!device.available || !actuator}
  class:on={device.state === "on"}
>
  <span class="name">{device.name}</span>
  <span class="room">{device.room}</span>
  {#if device.available}
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
  }
  button.on { background: color-mix(in srgb, var(--accent) 14%, var(--surface)); }
  button[aria-disabled="true"] { opacity: 0.55; }
  .name { font-size: var(--text-callout); font-weight: 600; }
  .room, .state { font-size: var(--text-caption-1); color: var(--label-secondary); text-transform: capitalize; }
  .warn { color: var(--warning); }
</style>
