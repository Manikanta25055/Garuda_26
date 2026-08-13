<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";

  let { onadded, oncancel } = $props();

  let types = $state({});
  // The server owns the channel map. Hardcoding 1–7 here would offer a channel
  // this deployment may not have, and a user picks a channel precisely so they
  // never touch a BCM pin.
  let channels = $state([]);
  let type = $state("light");
  let name = $state("");
  let room = $state("");
  let kind = $state("relay");
  let channel = $state(null);
  let topicBase = $state("");
  let error = $state("");
  let busy = $state(false);

  onMount(async () => {
    const body = await api.get("/api/drishti/device-types");
    types = body.types ?? {};
    channels = body.channels ?? [];
    type = Object.keys(types)[0] ?? "light";
    channel = channels[0] ?? null;
  });

  function idFrom(value) {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
      .replace(/^([^a-z])/, "d$1").slice(0, 32);
  }

  async function submit(event) {
    event.preventDefault();
    error = "";

    const id = idFrom(name);
    if (id.length < 2) {
      // The server's own message names the id it rejected, which reads as
      // nonsense when the id is a single letter the person never typed.
      error = "Give the device a longer name — at least two letters or digits.";
      return;
    }

    busy = true;
    try {
      await api.post("/api/drishti/devices", {
        id,
        name: name.trim(),
        type,
        room: room.trim(),
        transport: kind === "relay"
          ? { kind: "relay", channel: Number(channel) }
          : { kind: "mqtt", topic_base: topicBase.trim() },
      });
      onadded();
    } catch (err) {
      error = err.detail ?? "Could not add the device.";
    } finally {
      busy = false;
    }
  }
</script>

<form onsubmit={submit}>
  <h2>Add a device</h2>

  <label for="type">Type</label>
  <select id="type" bind:value={type}>
    {#each Object.keys(types) as typeName}<option value={typeName}>{typeName}</option>{/each}
  </select>

  <label for="name">Name</label>
  <input id="name" bind:value={name} placeholder="Desk lamp" />

  <label for="room">Room</label>
  <input id="room" bind:value={room} placeholder="Study" />

  <label for="kind">Connection</label>
  <select id="kind" bind:value={kind}>
    <option value="relay">Relay channel</option>
    <option value="mqtt">Wi-Fi (MQTT)</option>
  </select>

  {#if kind === "relay"}
    <label for="channel">Channel</label>
    <select id="channel" bind:value={channel}>
      {#each channels as c}<option value={c}>{c}</option>{/each}
    </select>
  {:else}
    <label for="topic">Topic</label>
    <input id="topic" bind:value={topicBase} placeholder="drishti/heater" />
  {/if}

  {#if error}<p class="err" role="alert">{error}</p>{/if}

  <div class="actions">
    <button type="button" class="cancel" onclick={oncancel}>Cancel</button>
    <button type="submit" class="go" disabled={busy}>Add device</button>
  </div>
</form>

<style>
  form { display: grid; gap: var(--space-1); }
  h2 { font-size: var(--text-title-3); font-weight: 600; margin: 0 0 var(--space-2); }
  label { font-size: var(--text-subhead); color: var(--label-secondary); margin-top: var(--space-2); }
  input, select {
    min-height: 44px;
    padding: 0 var(--space-3);
    border-radius: var(--radius-control);
    border: 1px solid var(--separator);
    background: var(--bg-secondary);
  }
  .err { color: var(--danger); font-size: var(--text-footnote); margin: var(--space-2) 0 0; }
  .actions { display: flex; gap: var(--space-2); margin-top: var(--space-5); }
  .actions button { flex: 1; min-height: 44px; border-radius: var(--radius-control); font-weight: 600; }
  .cancel { background: var(--bg-secondary); }
  .go { background: var(--accent); color: #fff; }
</style>
