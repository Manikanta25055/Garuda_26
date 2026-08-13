<script>
  let { proposal, onconfirm, ondiscard } = $props();
</script>

<article>
  <p class="lede">Here's what I understood.</p>
  <h3>{proposal.rule.source_utterance}</h3>

  <div class="chips">
    <span class="chip">When {proposal.rendered?.when}</span>
    <span class="chip">Then {proposal.rendered?.then}</span>
  </div>

  {#if proposal.conflict}
    <div class="conflict" role="alert">
      <strong>Conflicts with a rule you already have.</strong>
      <p>{proposal.conflict.source_utterance}</p>
      <p class="hint">Both drive the same device the opposite way. Saving keeps both — the earlier rule wins when they overlap.</p>
    </div>
  {/if}

  <div class="actions">
    <button class="save" onclick={() => onconfirm(proposal.id)}>Save</button>
    <button class="discard" onclick={() => ondiscard(proposal.id)}>Discard</button>
  </div>
</article>

<style>
  article {
    background: var(--surface);
    border: 1px solid var(--accent);
    border-radius: var(--radius-card);
    padding: var(--space-3);
    display: grid;
    gap: var(--space-2);
  }
  .lede { margin: 0; font-size: var(--text-footnote); color: var(--label-secondary); }
  h3 { margin: 0; font-size: var(--text-headline); line-height: var(--lh-headline); font-weight: 600; }
  .chips { display: flex; flex-wrap: wrap; gap: var(--space-2); }
  .chip {
    border-radius: calc(var(--radius-card) - var(--space-3));
    padding: var(--space-1) var(--space-2);
    font-size: var(--text-footnote);
    background: var(--bg-secondary);
    color: var(--label-secondary);
  }
  .conflict {
    border-radius: calc(var(--radius-card) - var(--space-3));
    padding: var(--space-2);
    background: color-mix(in srgb, var(--warning) 16%, var(--bg));
  }
  .conflict p { margin: var(--space-1) 0 0; font-size: var(--text-footnote); }
  .hint { color: var(--label-secondary); }
  .actions { display: flex; gap: var(--space-2); }
  .actions button { min-height: 44px; flex: 1; border-radius: var(--radius-control); font-weight: 600; }
  .save { background: var(--accent); color: #fff; }
  .discard { background: var(--bg-secondary); color: var(--label); }
</style>
