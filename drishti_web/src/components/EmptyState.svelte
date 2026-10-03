<script>
  let { title, body } = $props();
</script>

<div class="empty">
  <h3>{title}</h3>
  <p>{body}</p>
</div>

<style>
  /* An empty screen says what to do next, not that something is missing. */
  .empty {
    border: 1px dashed var(--separator);
    border-radius: var(--radius-card);
    padding: var(--space-5) var(--space-4);
    display: grid;
    gap: var(--space-1);
    text-align: center;
  }
  h3 { margin: 0; font-size: var(--text-headline); font-weight: var(--weight-semibold); }
  p { margin: 0; color: var(--label-secondary); font-size: var(--text-subhead); }
</style>
