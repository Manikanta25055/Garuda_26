import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import Composer from "../src/components/Composer.svelte";

function stub(body, status = 200) {
  vi.stubGlobal("fetch", () =>
    Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) }));
}

async function type(text) {
  await fireEvent.input(screen.getByRole("textbox"), { target: { value: text } });
  await fireEvent.click(screen.getByRole("button", { name: /send/i }));
}

describe("composer", () => {
  it("shows a local answer inline and clears the field", async () => {
    stub({ lane: "local", ok: true, kind: "state",
           text: "Yes — 1 person in the room right now.", resolved: "on-device" });
    render(Composer, { onresult: () => {} });

    await type("is anyone home?");

    expect(await screen.findByText(/1 person in the room/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("marks a local answer as resolved on device", async () => {
    stub({ lane: "local", ok: true, kind: "state", text: "It is 24°C.", resolved: "on-device" });
    render(Composer, { onresult: () => {} });
    await type("what's the temperature?");
    expect(await screen.findByText(/on device/i)).toBeInTheDocument();
  });

  it("hands a compiled proposal to the parent instead of rendering a bubble", async () => {
    const onresult = vi.fn();
    stub({ lane: "compile", ok: true, resolved: "compiled", proposal_id: "abc",
           rule: { source_utterance: "turn the fan off when the room is empty" },
           rendered: { when: "occupancy == empty", then: "fan → off" }, conflict: null });
    render(Composer, { onresult });

    await type("turn the fan off when the room is empty");

    await waitFor(() => expect(onresult).toHaveBeenCalled());
    expect(onresult.mock.calls[0][0].proposal_id).toBe("abc");
    expect(screen.queryByText(/turn the fan off when/)).toBeNull();
  });

  it("shows the refusal reason and says rules still fire", async () => {
    stub({ lane: "compile", ok: false, resolved: "compiled",
           reason: "could not reach the rule service: ConnectionError",
           still_working: true, vocabulary: ["occupancy", "hour"] });
    render(Composer, { onresult: () => {} });

    await type("dim the hallway when it rains");

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the rule service/);
    expect(screen.getByRole("alert")).toHaveTextContent(/still running/i);
  });

  it("reports an already-known rule rather than compiling again", async () => {
    stub({ lane: "known", ok: true, resolved: "already-known",
           rule: { id: "r_1", source_utterance: "turn the fan off when the room is empty" },
           rendered: { when: "occupancy == empty", then: "fan → off" } });
    render(Composer, { onresult: () => {} });

    await type("when the room's empty switch the fan off");

    expect(await screen.findByText(/already knows/i)).toBeInTheDocument();
  });

  it("refuses to send an empty instruction", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(Composer, { onresult: () => {} });
    await fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses to send whitespace", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(Composer, { onresult: () => {} });
    await type("   ");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the trimmed instruction", async () => {
    const fetchMock = vi.fn(() => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ lane: "local", ok: true, kind: "state", text: "ok" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(Composer, { onresult: () => {} });

    await type("  is anyone home?  ");

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body))
      .toEqual({ text: "is anyone home?" });
  });

  it("keeps what was typed when the send fails, so it is not lost", async () => {
    stub({ detail: "rate limited" }, 429);
    render(Composer, { onresult: () => {} });
    await type("turn the lamp on at sunset");
    expect(await screen.findByRole("alert")).toHaveTextContent(/rate limited/);
    expect(screen.getByRole("textbox")).toHaveValue("turn the lamp on at sunset");
  });

  it("says the house is unreachable and that rules still run", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));
    render(Composer, { onresult: () => {} });
    await type("turn the lamp on at sunset");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/can't reach the house/i);
    expect(alert).toHaveTextContent(/still running/i);
  });

  it("clears the previous answer when a new instruction is sent", async () => {
    stub({ lane: "local", ok: true, kind: "state", text: "It is 24°C.", resolved: "on-device" });
    render(Composer, { onresult: () => {} });
    await type("what's the temperature?");
    await screen.findByText(/24°C/);

    stub({ lane: "local", ok: true, kind: "state", text: "Nobody is home.", resolved: "on-device" });
    await type("is anyone home?");

    expect(await screen.findByText(/Nobody is home/)).toBeInTheDocument();
    expect(screen.queryByText(/24°C/)).toBeNull();
  });

  it("does not render a transcript", async () => {
    stub({ lane: "local", ok: true, kind: "state", text: "It is 24°C.", resolved: "on-device" });
    render(Composer, { onresult: () => {} });
    await type("what's the temperature?");
    await screen.findByText(/24°C/);
    // The question itself is never echoed back as a message.
    expect(screen.queryByText(/what's the temperature/i)).toBeNull();
  });
});
