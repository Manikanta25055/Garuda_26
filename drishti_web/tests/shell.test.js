import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import OfflineBanner from "../src/components/OfflineBanner.svelte";

describe("offline banner", () => {
  it("says what still works", () => {
    render(OfflineBanner, { offline: true });
    const alert = screen.getByRole("status");
    expect(alert).toHaveTextContent(/rules are still running/i);
  });

  it("shows nothing when online", () => {
    render(OfflineBanner, { offline: false });
    expect(screen.queryByRole("status")).toBeNull();
  });
});
