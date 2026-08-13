<script>
  let { active } = $props();
</script>

<figure>
  {#if active}
    <img src="/api/drishti/stream" alt="Live camera view" />
  {:else}
    <div class="placeholder">Camera paused</div>
  {/if}
</figure>

<style>
  figure { margin: 0; border-radius: var(--radius-card); overflow: hidden; background: #000; }
  img { display: block; width: 100%; height: auto; }
  .placeholder {
    aspect-ratio: 4 / 3; display: grid; place-items: center;
    color: var(--label-tertiary); font-size: var(--text-footnote);
  }
</style>
