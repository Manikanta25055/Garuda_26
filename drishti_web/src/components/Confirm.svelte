<script>
  let { open, title, body, confirmLabel, onconfirm, oncancel } = $props();

  function onkeydown(event) {
    if (event.key === "Escape") oncancel();
  }
</script>

<svelte:window on:keydown={open ? onkeydown : undefined} />

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="scrim" onclick={oncancel}>
    <div class="sheet" role="dialog" aria-modal="true" aria-label={title} tabindex="-1"
         onclick={(event) => event.stopPropagation()}>
      <h2>{title}</h2>
      <p>{body}</p>
      <div class="actions">
        <button class="cancel" onclick={oncancel}>Cancel</button>
        <button class="go" onclick={onconfirm}>{confirmLabel}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed; inset: 0; z-index: 40;
    display: grid; align-items: end;
    background: rgb(0 0 0 / 0.4);
  }
  /* The sheet rises from the bottom, where the action was summoned. */
  .sheet {
    background: var(--surface);
    border-radius: var(--radius-sheet) var(--radius-sheet) 0 0;
    padding: var(--space-5) var(--margin-content);
    padding-bottom: max(var(--space-5), env(safe-area-inset-bottom));
    display: grid; gap: var(--space-2);
  }
  h2 { margin: 0; font-size: var(--text-title-3); font-weight: 600; }
  p { margin: 0; color: var(--label-secondary); }
  .actions { display: flex; gap: var(--space-2); margin-top: var(--space-3); }
  .actions button {
    flex: 1; min-height: 44px;
    border-radius: calc(var(--radius-sheet) - var(--space-5));
    font-weight: 600;
  }
  .cancel { background: var(--bg-secondary); }
  .go { background: var(--danger); color: #fff; }
</style>
