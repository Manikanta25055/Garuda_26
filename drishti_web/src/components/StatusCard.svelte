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
  /* Title-2, not large-title. The screen already has one large title above
     this, and two stacked compete rather than establish a hierarchy. */
  h2 {
    margin: 0;
    font-size: var(--text-title-2);
    line-height: var(--lh-title-2);
    font-weight: var(--weight-semibold);
    letter-spacing: -0.01em;
  }
  p { margin: 0; color: var(--label-secondary); font-size: var(--text-body); }
</style>
