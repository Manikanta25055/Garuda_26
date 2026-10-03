<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { house } from "../lib/app.svelte.js";
  import { session } from "../lib/session.svelte.js";
  import AddDevice from "./AddDevice.svelte";
  import EmptyState from "../components/EmptyState.svelte";
  import Confirm from "../components/Confirm.svelte";

  let adding = $state(false);
  let pendingDelete = $state(null);
  let notice = $state("");

  onMount(() => {
    house.loadDevices();
    house.loadState();
  });

  async function removeDevice() {
    const device = pendingDelete;
    pendingDelete = null;
    if (!device) return;
    const body = await api.del(`/api/drishti/devices/${device.id}`);
    await house.loadDevices();
    const orphaned = body?.orphaned ?? 0;
    notice = orphaned
      ? `Removed ${device.name}. ${orphaned} rule${orphaned === 1 ? "" : "s"} now need repair — they are kept, not deleted.`
      : `Removed ${device.name}.`;
  }

  async function deviceAdded() {
    adding = false;
    notice = "";
    await house.loadDevices();
  }
</script>

<h1>Settings</h1>

<!-- Two things a person living here can actually change: what is in the house,
     and whether they are signed in. The rest of what used to be on this screen
     was either a heading with nothing behind it or a readout of how many times
     the rule loop had evaluated — written for whoever built this, not for
     whoever lives here. -->

<section>
  <h2>Devices</h2>

  {#if adding}
    <AddDevice onadded={deviceAdded} oncancel={() => (adding = false)} />
  {:else}
    {#if house.devices.length === 0}
      <EmptyState
        title="No devices yet"
        body="Add one, then tell the house what to do with it."
      />
    {:else}
      <ul>
        {#each house.devices as device (device.id)}
          <li>
            <span class="name">{device.name}</span>
            <span class="meta">{device.room} · {device.type}</span>
            <button class="del" onclick={() => (pendingDelete = device)}
                    aria-label={`Remove ${device.name}`}>Remove</button>
          </li>
        {/each}
      </ul>
    {/if}
    <button class="add" onclick={() => (adding = true)}>Add a device</button>
  {/if}

  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
</section>

<section>
  <h2>Privacy</h2>
  <p class="muted">
    Teaching a new rule sends your sentence and the names of your devices to the
    rule service. It never sends a camera frame, and never sends what any sensor
    is reading right now.
  </p>
</section>

<section>
  <h2>Account</h2>
  <p class="muted">Signed in as {session.username}.</p>
  <button class="signout" onclick={() => session.signOut()}>Sign out</button>
</section>

<Confirm
  open={pendingDelete !== null}
  title={pendingDelete ? `Remove ${pendingDelete.name}?` : ""}
  body="Rules that use it are kept and marked as needing repair, so nothing you taught is lost."
  confirmLabel="Remove"
  onconfirm={removeDevice}
  oncancel={() => (pendingDelete = null)}
/>

<style>
  section {
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-radius: var(--radius-card);
    padding: var(--space-4);
    margin-bottom: var(--space-4);
  }
  h2 {
    font-size: var(--text-title-3);
    line-height: var(--lh-title-3);
    font-weight: var(--weight-semibold);
    letter-spacing: -0.01em;
    margin: 0 0 var(--space-2);
  }
  .muted { color: var(--label-secondary); font-size: var(--text-subhead); margin: 0; }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }
  li {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    background: var(--bg-secondary);
    /* Concentric: parent radius 20 minus 16 padding. */
    border-radius: calc(var(--radius-card) - var(--space-4));
    padding: var(--space-3);
  }
  .name { font-weight: var(--weight-semibold); }
  .meta { grid-column: 1; font-size: var(--text-caption-1); color: var(--label-secondary); }
  .del {
    grid-column: 2; grid-row: 1 / span 2;
    min-height: 44px;
    color: var(--danger);
    font-size: var(--text-footnote);
  }
  .add, .signout {
    margin-top: var(--space-3);
    width: 100%;
    min-height: 44px;
    border-radius: var(--radius-control);
    background: var(--bg-secondary);
    font-weight: var(--weight-semibold);
    transition: background var(--dur-fast) var(--ease-standard);
  }
  .add:hover { background: color-mix(in srgb, var(--accent) 12%, var(--bg-secondary)); }
  .signout { color: var(--danger); }
  .signout:hover { background: color-mix(in srgb, var(--danger) 10%, var(--bg-secondary)); }
  .notice { margin: var(--space-3) 0 0; font-size: var(--text-footnote); color: var(--label-secondary); }

  @media (prefers-reduced-motion: reduce) {
    .add, .signout { transition: none; }
  }
</style>
