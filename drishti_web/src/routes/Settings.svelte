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

  const admin = $derived(session.role === "admin");

  const loop = (house.state.rule_loop);

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

<div class="sections">
<section class="wide">
  <h2>Devices and rooms</h2>

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

{#if admin}
  <section>
    <h2>People</h2>
    <p class="muted">Who can sign in to Drishti. Managed in Garuda for now.</p>
  </section>

  <section>
    <h2>Alerts</h2>
    <p class="muted">Where Garuda sends danger and tamper alerts.</p>
  </section>

  <section>
    <h2>Automation</h2>
    <p class="muted">
      Rules run on this device. Teaching a new one sends the sentence and the
      names of your devices to the rule service — never a camera frame, and
      never any current reading.
    </p>
  </section>

  <section>
    <h2>System</h2>
    <p class="muted">Signed in as {session.username} ({session.role}).</p>
    {#if loop}
      <dl>
        <dt>Rule loop</dt>
        <dd>{loop.running ? "running" : "stopped"}</dd>
        <dt>Evaluated</dt>
        <dd>{loop.ticks} times</dd>
        <dt>Actions taken</dt>
        <dd>{loop.fires}</dd>
        <dt>Rules</dt>
        <dd>{loop.rules}{loop.orphaned_rules ? " (+" + loop.orphaned_rules + " needing repair)" : ""}</dd>
        <dt>Camera</dt>
        <dd>{house.state.pipeline ?? "unknown"}</dd>
      </dl>
      {#if loop.last_error}
        <p class="muted" role="alert">Last error: {loop.last_error}</p>
      {/if}
    {/if}
  </section>
{:else}
  <section>
    <h2>Account</h2>
    <p class="muted">Signed in as {session.username}. Ask an admin for anything else.</p>
  </section>
{/if}

</div>

<button class="signout" onclick={() => session.signOut()}>Sign out</button>

<Confirm
  open={pendingDelete !== null}
  title={pendingDelete ? `Remove ${pendingDelete.name}?` : ""}
  body="Rules that use it are kept and marked as needing repair, so nothing you taught is lost."
  confirmLabel="Remove"
  onconfirm={removeDevice}
  oncancel={() => (pendingDelete = null)}
/>

<style>
  /* On a laptop the sections sit side by side; a single 15rem-indented column
     of short paragraphs down a 1440px window is mostly empty space. */
  .sections { display: grid; gap: var(--space-4); }
  @media (min-width: 900px) {
    .sections { grid-template-columns: repeat(auto-fill, minmax(22rem, 1fr)); align-items: start; }
    .sections > .wide { grid-column: 1 / -1; }
  }
  section {
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-radius: var(--radius-card);
    padding: var(--space-4);
  }
  h2 {
    font-size: var(--text-title-3);
    line-height: var(--lh-title-3);
    font-weight: 600;
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
  .name { font-weight: 600; }
  .meta { grid-column: 1; font-size: var(--text-caption-1); color: var(--label-secondary); }
  .del { grid-column: 2; grid-row: 1 / span 2; min-height: 44px; color: var(--danger); font-size: var(--text-footnote); }
  .add, .signout {
    margin-top: var(--space-3);
    width: 100%; min-height: 44px;
    border-radius: var(--radius-control);
    background: var(--bg-secondary);
    font-weight: 600;
    transition: background var(--dur-fast) var(--ease-standard);
  }
  .add:hover { background: color-mix(in srgb, var(--accent) 12%, var(--bg-secondary)); }
  .signout { margin-top: var(--space-6); color: var(--danger); max-width: 22rem; }
  .signout:hover { background: color-mix(in srgb, var(--danger) 10%, var(--bg-secondary)); }
  .notice { margin: var(--space-3) 0 0; font-size: var(--text-footnote); color: var(--label-secondary); }
  dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: var(--space-1) var(--space-3); }
  dt { color: var(--label-secondary); font-size: var(--text-footnote); }
  dd { margin: 0; font-size: var(--text-footnote); font-variant-numeric: tabular-nums; }
</style>
