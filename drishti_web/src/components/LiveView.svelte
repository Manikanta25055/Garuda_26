<script>
  let { privacy, onprivacy } = $props();
  let busy = $state(false);

  async function toggle() {
    if (busy) return;
    busy = true;
    try {
      await onprivacy(!privacy);
    } finally {
      busy = false;
    }
  }
</script>

<!-- A positioned stack. The frame fills it, and anything drawn over the video
     later -- detection boxes as SVG -- is absolutely positioned inside without
     moving the layout. -->
<figure class="stage">
  {#if privacy}
    <div class="off">
      <p>The camera is off</p>
    </div>
  {:else}
    <img src="/api/drishti/stream" alt="Live camera view" />
  {/if}

  <button
    class="privacy"
    role="switch"
    aria-checked={privacy}
    aria-label="Camera"
    aria-busy={busy}
    onclick={toggle}
  >{privacy ? "Turn camera on" : "Turn camera off"}</button>
</figure>

<style>
  /* A fixed shape, so the layout is settled before the first frame lands and
     nothing jumps when it does. */
  .stage {
    position: relative;
    margin: 0;
    aspect-ratio: 16 / 9;
    border-radius: var(--radius-card);
    overflow: hidden;
    background: #000;
  }

  /* cover, not contain: the box is sized by the layout, and a sensor that does
     not match it should be cropped rather than letterboxed into grey bars. */
  img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .off {
    display: grid;
    place-items: center;
    height: 100%;
    color: var(--label-tertiary);
    font-size: var(--text-subhead);
  }
  .off p { margin: 0; }

  .privacy {
    position: absolute;
    right: var(--space-3);
    bottom: var(--space-3);
    min-height: 44px;
    padding: 0 var(--space-4);
    border-radius: 9999px;
    font-size: var(--text-footnote);
    font-weight: var(--weight-semibold);
    color: #fff;
    /* Its own glass, over video rather than over the rail: the two never
       overlap, so this is not glass stacked on glass. */
    background: rgb(0 0 0 / 0.45);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    transition: background var(--dur-fast) var(--ease-standard);
  }
  .privacy:hover { background: rgb(0 0 0 / 0.6); }
  .privacy[aria-busy="true"] { opacity: 0.6; }

  @media (prefers-reduced-motion: reduce) {
    .privacy { transition: none; }
  }
  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .privacy {
      background: rgb(0 0 0 / 0.9);
      backdrop-filter: none;
      -webkit-backdrop-filter: none;
    }
  }
</style>
