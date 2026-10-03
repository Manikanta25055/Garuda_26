<script>
  import { onMount } from "svelte";
  import { house } from "../lib/app.svelte.js";
  import { relativeTime } from "../lib/format.js";
  import EmptyState from "../components/EmptyState.svelte";

  onMount(() => house.loadActivity());

  function conditions(entry) {
    return (entry.matched ?? []).map((c) => `${c.field} ${c.op} ${c.value}`).join(" and ");
  }
</script>

<h1>Activity</h1>

{#if house.activity.length === 0}
  <EmptyState
    title="Nothing has happened yet"
    body="Once your rules start firing, every action shows up here with the reason behind it."
  />
{:else}
  <ol>
    {#each house.activity as entry, index (entry.ts + entry.device + index)}
      <li class:failed={!entry.ok}>
        <p class="what">
          {#if entry.ok}
            <strong>{entry.device}</strong> turned {entry.action}
          {:else}
            <strong>{entry.device}</strong> didn't work — tried to turn {entry.action}
          {/if}
        </p>
        {#if (entry.matched ?? []).length > 0}
          <p class="why">{conditions(entry)}</p>
        {/if}
        {#if !entry.ok && entry.reason}
          <p class="why">{entry.reason}</p>
        {/if}
        <span class="when">{relativeTime(entry.ts)}</span>
      </li>
    {/each}
  </ol>
{/if}

<style>
  ol { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }
  /* Two columns once the column itself is wide enough for two cards -- not
     once the window is. auto-fill rather than a fixed count, so there is no
     width at which a gap is left over. */
  @container panel (min-width: 44rem) {
    ol { grid-template-columns: repeat(auto-fill, minmax(22rem, 1fr)); align-items: start; }
  }
  li {
    background: var(--surface);
    border: 0.5px solid var(--separator);
    /* The stripe repeats what the sentence already says; it never carries the
       outcome on its own. */
    border-left: 3px solid var(--success);
    border-radius: var(--radius-card);
    padding: var(--space-3);
  }
  li.failed { border-left-color: var(--danger); }
  .what { margin: 0; }
  .why, .when { font-size: var(--text-caption-1); color: var(--label-secondary); }
  .why { margin: var(--space-1) 0 0; }
</style>
