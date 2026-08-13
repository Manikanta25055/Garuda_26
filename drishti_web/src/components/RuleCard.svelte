<script>
  import { relativeTime, firedCount } from "../lib/format.js";
  let { rule, devices, ontoggle, ondelete } = $props();
</script>

<article class:orphaned={rule.orphaned}>
  <h3>{rule.source_utterance}</h3>

  <div class="chips">
    <span class="chip when">When {rule.rendered?.when}</span>
    <span class="chip then">Then {rule.rendered?.then}</span>
  </div>

  <footer>
    {#if rule.orphaned}
      <span class="repair">Needs repair — a device it uses was removed</span>
    {:else}
      <span class="meta">{firedCount(rule.fired_count)} · {relativeTime(rule.last_fired)}</span>
      <button
        role="switch"
        aria-checked={rule.enabled}
        aria-label="Enabled"
        onclick={() => ontoggle(rule.id)}
      >
        <span class="track" aria-hidden="true"></span>
        <span class="word">{rule.enabled ? "On" : "Paused"}</span>
      </button>
    {/if}
    <button class="del" onclick={() => ondelete(rule.id)} aria-label="Delete rule">Delete</button>
  </footer>
</article>

<style>
  /* Content layer — a solid surface, never glass. */
  article {
    background: var(--surface);
    border: 0.5px solid var(--separator);
    border-radius: var(--radius-card);
    padding: var(--space-3);
    display: grid;
    gap: var(--space-2);
  }
  article.orphaned { border-color: color-mix(in srgb, var(--warning) 60%, var(--separator)); }
  h3 {
    margin: 0;
    font-size: var(--text-headline);
    line-height: var(--lh-headline);
    font-weight: 600;
  }
  .chips { display: flex; flex-wrap: wrap; gap: var(--space-2); }
  .chip {
    /* Concentric: parent radius 20 minus 12 padding. */
    border-radius: calc(var(--radius-card) - var(--space-3));
    padding: var(--space-1) var(--space-2);
    font-size: var(--text-footnote);
    background: var(--bg-secondary);
    color: var(--label-secondary);
  }
  footer { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
  .meta, .repair { font-size: var(--text-caption-1); color: var(--label-secondary); }
  [role="switch"] { min-height: 44px; display: flex; align-items: center; gap: var(--space-2); }
  .track {
    width: 42px; height: 26px; border-radius: 9999px;
    background: var(--separator);
    position: relative;
    transition: background var(--dur-fast) var(--ease-standard);
  }
  .track::after {
    content: ""; position: absolute; top: 2px; left: 2px;
    width: 22px; height: 22px; border-radius: 9999px; background: #fff;
    transition: transform var(--dur-fast) var(--ease-standard);
  }
  [aria-checked="true"] .track { background: var(--success); }
  [aria-checked="true"] .track::after { transform: translateX(16px); }
  /* The word carries the state too — colour alone never does. */
  .word { font-size: var(--text-caption-1); color: var(--label-secondary); }
  .del { min-height: 44px; margin-left: auto; color: var(--danger); font-size: var(--text-footnote); }
</style>
