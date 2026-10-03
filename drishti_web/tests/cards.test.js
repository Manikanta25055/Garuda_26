import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import RuleCard from "../src/components/RuleCard.svelte";
import ProposalCard from "../src/components/ProposalCard.svelte";
import { relativeTime, deviceName, firedCount } from "../src/lib/format.js";

const DEVICES = [{ id: "fan", name: "Fan", room: "study" }];

const RULE = {
  id: "r_1",
  source_utterance: "turn the fan off when the room is empty for five minutes",
  rendered: { when: "occupancy == empty and occupancy_duration_s >= 300", then: "fan → off" },
  enabled: true,
  fired_count: 12,
  last_fired: 1_754_000_000,
};

describe("rule card", () => {
  it("uses the spoken sentence as the title", () => {
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle: () => {}, ondelete: () => {} });
    expect(screen.getByRole("heading"))
      .toHaveTextContent("turn the fan off when the room is empty for five minutes");
  });

  it("shows the conditions and actions as chips", () => {
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle: () => {}, ondelete: () => {} });
    expect(screen.getByText(/occupancy == empty/)).toBeInTheDocument();
    expect(screen.getByText(/fan → off/)).toBeInTheDocument();
  });

  it("reports how often it has fired", () => {
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle: () => {}, ondelete: () => {} });
    expect(screen.getByText(/12 times/i)).toBeInTheDocument();
  });

  it("toggles", async () => {
    const ontoggle = vi.fn();
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle, ondelete: () => {} });
    await fireEvent.click(screen.getByRole("switch"));
    expect(ontoggle).toHaveBeenCalledWith("r_1");
  });

  it("deletes", async () => {
    const ondelete = vi.fn();
    render(RuleCard, { rule: RULE, devices: DEVICES, ontoggle: () => {}, ondelete });
    await fireEvent.click(screen.getByRole("button", { name: /delete rule/i }));
    expect(ondelete).toHaveBeenCalledWith("r_1");
  });

  it("marks an orphaned rule and does not offer to enable it", () => {
    render(RuleCard, {
      rule: { ...RULE, orphaned: true, enabled: false },
      devices: [], ontoggle: () => {}, ondelete: () => {},
    });
    expect(screen.getByText(/needs repair/i)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("still lets an orphaned rule be deleted", async () => {
    const ondelete = vi.fn();
    render(RuleCard, {
      rule: { ...RULE, orphaned: true, enabled: false },
      devices: [], ontoggle: () => {}, ondelete,
    });
    await fireEvent.click(screen.getByRole("button", { name: /delete rule/i }));
    expect(ondelete).toHaveBeenCalledWith("r_1");
  });

  it("states enabled in words, not only colour", () => {
    render(RuleCard, {
      rule: { ...RULE, enabled: false }, devices: DEVICES,
      ontoggle: () => {}, ondelete: () => {},
    });
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/paused/i)).toBeInTheDocument();
  });

  it("survives a rule the server rendered without chips", () => {
    render(RuleCard, {
      rule: { ...RULE, rendered: undefined }, devices: DEVICES,
      ontoggle: () => {}, ondelete: () => {},
    });
    expect(screen.getByRole("heading")).toBeInTheDocument();
  });
});

describe("proposal card", () => {
  const PROPOSAL = {
    id: "abc",
    rule: { source_utterance: "turn the fan off when the room is empty" },
    rendered: { when: "occupancy == empty", then: "fan → off" },
    conflict: null,
  };

  it("asks before saving", () => {
    render(ProposalCard, { proposal: PROPOSAL, onconfirm: () => {}, ondiscard: () => {} });
    expect(screen.getByText(/here's what i understood/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /discard/i })).toBeInTheDocument();
  });

  it("confirms and discards through callbacks", async () => {
    const onconfirm = vi.fn();
    const ondiscard = vi.fn();
    render(ProposalCard, { proposal: PROPOSAL, onconfirm, ondiscard });
    await fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onconfirm).toHaveBeenCalledWith("abc");
    await fireEvent.click(screen.getByRole("button", { name: /discard/i }));
    expect(ondiscard).toHaveBeenCalledWith("abc");
  });

  it("never saves on its own", () => {
    const onconfirm = vi.fn();
    render(ProposalCard, { proposal: PROPOSAL, onconfirm, ondiscard: () => {} });
    // A rule can pass every check and still mean the opposite of what was
    // asked. Nothing but the person's tap may commit it.
    expect(onconfirm).not.toHaveBeenCalled();
  });

  it("shows both rules when there is a conflict", () => {
    render(ProposalCard, {
      proposal: { ...PROPOSAL, conflict: { id: "r_9", source_utterance: "keep the fan on while I'm at the desk" } },
      onconfirm: () => {}, ondiscard: () => {},
    });
    expect(screen.getByText(/conflicts with/i)).toBeInTheDocument();
    expect(screen.getByText(/keep the fan on while I'm at the desk/)).toBeInTheDocument();
  });

  it("says nothing about conflicts when there are none", () => {
    render(ProposalCard, { proposal: PROPOSAL, onconfirm: () => {}, ondiscard: () => {} });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("format", () => {
  it("says never for a timestamp that does not exist", () => {
    expect(relativeTime(0)).toBe("never");
    expect(relativeTime(undefined)).toBe("never");
  });

  it("scales the unit with the distance", () => {
    const now = Date.now() / 1000;
    expect(relativeTime(now - 5)).toBe("just now");
    expect(relativeTime(now - 300)).toBe("5 min ago");
    expect(relativeTime(now - 7200)).toBe("2 h ago");
    expect(relativeTime(now - 172_800)).toBe("2 d ago");
  });

  it("never reports a future timestamp as negative", () => {
    expect(relativeTime(Date.now() / 1000 + 600)).toBe("just now");
  });

  it("falls back to the id when a device is gone", () => {
    expect(deviceName(DEVICES, "fan")).toBe("Fan");
    expect(deviceName(DEVICES, "lamp")).toBe("lamp");
    expect(deviceName([], "lamp")).toBe("lamp");
  });

  it("counts in words", () => {
    expect(firedCount(0)).toBe("never fired");
    expect(firedCount(undefined)).toBe("never fired");
    expect(firedCount(1)).toBe("fired once");
    expect(firedCount(12)).toBe("fired 12 times");
  });
});
