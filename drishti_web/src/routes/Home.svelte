<script>
  import { onMount } from "svelte";
  import { house } from "../lib/app.svelte.js";
  import { setDevice, allOff } from "../lib/control.js";
  import StatusCard from "../components/StatusCard.svelte";
  import LiveView from "../components/LiveView.svelte";
  import DeviceTile from "../components/DeviceTile.svelte";
  import EmptyState from "../components/EmptyState.svelte";
  import Confirm from "../components/Confirm.svelte";

  let confirmAllOff = $state(false);
  let notice = $state("");

  onMount(() => {
    house.loadState();
    house.loadDevices();
    const timer = setInterval(() => house.loadState(), 5000);
    return () => clearInterval(timer);
  });

  async function toggle(id, action) {
    await setDevice(id, action);
    await house.loadDevices();
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

<h1>Home</h1>

<StatusCard state={house.state} />
<LiveView active={!house.state.modes?.privacy} />

<h2>Devices</h2>
{#if house.devices.length === 0}
  <EmptyState
    title="No devices yet"
    body="Add your first device in Settings, then tell the house what to do with it."
  />
{:else}
  <div class="grid">
    {#each house.devices as device (device.id)}
      <DeviceTile {device} ontoggle={toggle} />
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
  h2 { font-size: var(--text-title-3); font-weight: 600; margin: var(--space-6) 0 var(--space-2); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--space-2); }
  .notice { margin: var(--space-3) 0 0; font-size: var(--text-footnote); color: var(--label-secondary); }
  /* Deliberate, separated, and confirmed — not adjacent to navigation. */
  .stop {
    margin-top: var(--space-8);
    width: 100%; min-height: 44px;
    border-radius: var(--radius-control);
    border: 1px solid var(--danger);
    color: var(--danger);
    font-weight: 600;
  }
</style>
