<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { house } from "../lib/app.svelte.js";
  import { setDevice, allOff } from "../lib/control.js";
  import StatusCard from "../components/StatusCard.svelte";
  import LiveView from "../components/LiveView.svelte";
  import DeviceTile from "../components/DeviceTile.svelte";
  import EmptyState from "../components/EmptyState.svelte";
  import Confirm from "../components/Confirm.svelte";

  let confirmAllOff = $state(false);
  let notice = $state("");
  let loaded = $state(false);
  let busy = $state(new Set());

  onMount(() => {
    Promise.all([house.loadState(), house.loadDevices()]).finally(() => (loaded = true));
    const timer = setInterval(() => house.loadState(), 5000);
    return () => clearInterval(timer);
  });

  async function toggle(id, action) {
    // The tile shows it is working. Without this the only feedback is the tile
    // changing several hundred milliseconds later, which reads as a dead tap.
    busy = new Set(busy).add(id);
    try {
      await setDevice(id, action);
      await house.loadDevices();
    } finally {
      const next = new Set(busy);
      next.delete(id);
      busy = next;
    }
  }

  // The camera needs an off switch the person living here can press.
  async function setPrivacy(on) {
    await api.post("/api/drishti/privacy", { on });
    await house.loadState();
  }

  async function turnEverythingOff() {
    confirmAllOff = false;
    const stopped = await allOff(house.devices);
    await house.loadDevices();
    notice = stopped.length
      ? `Turned off ${stopped.length} device${stopped.length === 1 ? "" : "s"}.`
      : "Everything was already off.";
  }
</script>

<h1>House</h1>

<StatusCard state={house.state} />

<LiveView privacy={!!house.state.modes?.privacy} onprivacy={setPrivacy} />

<h2 class="section-title">Devices</h2>

{#if !loaded}
  <div class="tiles" aria-hidden="true">
    {#each [1, 2, 3, 4] as n (n)}<div class="skeleton tile-skeleton"></div>{/each}
  </div>
  <p class="sr">Loading your devices.</p>
{:else if house.devices.length === 0}
  <EmptyState
    title="No devices yet"
    body="Add your first device in Settings, then tell the house what to do with it."
  />
{:else}
  <div class="tiles">
    {#each house.devices as device (device.id)}
      <DeviceTile {device} ontoggle={toggle} busy={busy.has(device.id)} />
    {/each}
  </div>
{/if}

{#if notice}<p class="notice" role="status">{notice}</p>{/if}

<button class="stop" onclick={() => (confirmAllOff = true)}>Turn everything off</button>

<Confirm
  open={confirmAllOff}
  title="Turn everything off?"
  body="Every reachable device switches off. Your rules keep running and may switch something back on."
  confirmLabel="Turn off"
  onconfirm={turnEverythingOff}
  oncancel={() => (confirmAllOff = false)}
/>

<style>

  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr));
    gap: var(--space-2);
  }
  .tile-skeleton { height: 5.5rem; }

  .notice { margin: var(--space-3) 0 0; font-size: var(--text-footnote); color: var(--label-secondary); }

  /* Deliberate, separated, and confirmed — not adjacent to navigation. */
  .stop {
    margin-top: var(--space-8);
    width: 100%;
    max-width: 22rem;
    min-height: 44px;
    border-radius: var(--radius-control);
    border: 1px solid var(--danger);
    color: var(--danger);
    font-weight: 600;
    transition: background var(--dur-fast) var(--ease-standard);
  }
  .stop:hover { background: color-mix(in srgb, var(--danger) 10%, transparent); }

  .sr {
    position: absolute; width: 1px; height: 1px;
    overflow: hidden; clip-path: inset(50%); white-space: nowrap;
  }
</style>
