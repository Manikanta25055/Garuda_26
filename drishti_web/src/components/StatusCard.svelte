<script>
  let { state } = $props();

  const occupied = $derived(state.occupancy === "occupied");
  const count = $derived(state.person_count ?? 0);
  const detail = $derived(
    occupied
      ? `${count} ${count === 1 ? "person" : "people"} in the room`
      : "The room is empty");
</script>

<!-- One line that answers the question, at a size you can read from across the
     room. The three stacked strings this replaced made you assemble the answer
     yourself, and one of them was how long the server process had been up. -->
<section>
  <h2>{occupied ? "Someone's home" : "Nobody's home"}</h2>
  <p>{detail}</p>
</section>

<style>
  section { display: grid; gap: var(--space-1); }
  h2 {
    margin: 0;
    font-size: var(--text-large-title);
    line-height: var(--lh-large-title);
    font-weight: var(--weight-bold);
    /* Negative tracking as the size grows, or the letters read too far apart. */
    letter-spacing: -0.02em;
  }
  p { margin: 0; color: var(--label-secondary); font-size: var(--text-body); }
</style>
