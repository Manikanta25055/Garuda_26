<script>
  let { offline } = $props();
</script>

{#if offline}
  <p role="status">
    Can't reach the network. Your rules are still running, and you can still
    control devices and ask about the house — new rules need a connection.
  </p>
{/if}

<style>
  /* Offline is a state, not an error: it says what still works. */
  p {
    margin: 0;
    padding: var(--space-2) var(--margin-content);
    background: color-mix(in srgb, var(--warning) 18%, var(--bg));
    color: var(--label);
    font-size: var(--text-footnote);
    line-height: var(--lh-footnote);
  }
</style>
