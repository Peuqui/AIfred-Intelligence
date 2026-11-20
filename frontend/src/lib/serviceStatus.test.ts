// The status line's rules (lib/serviceStatus.ts), as the Reflex page had them in assets/custom.js.
import { describe, expect, it } from "vitest";
import type { LocalHoldStatus, ServiceSnapshot } from "../api/types";
import { formatRemaining, hostRows, localHoldRow, serviceRows, summaryColor } from "./serviceStatus";

const LABELS = {
  lang: "de", noModel: "kein Modell", noneLoaded: "nichts", loading: "lädt", busy: "arbeitet",
  unreachable: "weg", hostHeld: "freigehalten von {owner}", hostRequested: "angefordert", hostHeldByOther: "gehalten von {owner}", hostUsed: "{used} von {total} MiB belegt",
};

const SNAPSHOT: ServiceSnapshot = {
  llm_reachable: true,
  models: [
    { name: "Big", role: "llm", state: "ready", busy: false, remaining_s: 300, exact: false },
    { name: "Eyes-visiond", role: "describer", state: "starting", busy: false, remaining_s: null, exact: true },
  ],
  stt_reachable: true,
  stt: [{ device: "cuda", engine: "parakeet", model: "tdt", remaining_s: 60 }, { device: "cpu", engine: "whisper", model: "s", remaining_s: null }],
  tts_reachable: false,
  tts: [],
};

describe("serviceRows", () => {
  const rows = serviceRows(SNAPSHOT, LABELS);

  it("a loaded idle model counts down at most and can be unloaded", () => {
    expect(rows.llm).toEqual([{ color: "green", text: "Big", remaining: 300, upperBound: true, action: { verb: "unload", service: "llm", item: "Big" } }]);
  });

  it("a model that loads has no time and no button", () => {
    expect(rows.describer[0]).toMatchObject({ color: "yellow", text: "Eyes-visiond · lädt", remaining: null, action: null });
  });

  it("only the GPU worker of the speech recognition unloads, the CPU model stays", () => {
    expect(rows.stt.map((row) => [row.text, row.action?.verb ?? null])).toEqual([["Parakeet · GPU", "unload"], ["Whisper · CPU", null]]);
  });

  it("a service that does not answer is red, one with nothing loaded grey and loadable where it can", () => {
    expect(rows.tts).toEqual([{ color: "red", text: "weg", remaining: null, upperBound: false, action: null }]);
    const empty = serviceRows({ ...SNAPSHOT, models: [] }, LABELS);
    expect(empty.llm[0]).toMatchObject({ color: "grey", text: "kein Modell", action: { verb: "load", service: "llm" } });
  });
});

describe("hostRows", () => {
  it("a held machine is amber and offers the release", () => {
    const holders = [{ consumer: "llama-swap", item: "eyes" }, { consumer: "tts-suite", item: "voice" }];
    const [row] = hostRows([{ name: "Aragon", reachable: true, held: "vllm", hold_state: "active", hold_owner: "vllm-research", hold_is_ours: false, used_mib: 1024, total_mib: 24576, holders, error: "" }], LABELS);
    expect(row).toMatchObject({ color: "amber", label: "Aragon", action: { verb: "release", service: "host", item: "Aragon" } });
    expect(row?.text).toBe("1.024 von 24.576 MiB belegt");
    expect(row?.detail).toBe("freigehalten von vllm-research: vllm\nllama-swap: eyes\ntts-suite: voice"); // visible without hover
    expect(row?.title).toBe("freigehalten von vllm-research: vllm");
    expect(row?.action?.blocked).toBe("gehalten von vllm-research"); // another owner's: greyed out
  });

  it("a requested hold is amber too and says so in the title", () => {
    const [row] = hostRows([{ name: "Aragon", reachable: true, held: "vllm", hold_state: "requested", hold_owner: "aifred-statusleiste", hold_is_ours: true, used_mib: 1, total_mib: 2, holders: [], error: "" }], LABELS);
    expect(row).toMatchObject({ color: "amber", title: "freigehalten von aifred-statusleiste (angefordert): vllm", action: { verb: "release" } });
    expect(row?.action?.blocked).toBeUndefined(); // the status bar's own: it may let go
    expect(row?.detail).toBe("freigehalten von aifred-statusleiste (angefordert): vllm");
  });

  it("a machine that does not answer says so as text, the error under it", () => {
    const [row] = hostRows([{ name: "Aragon", reachable: false, held: null, hold_state: null, hold_owner: null, hold_is_ours: false, used_mib: 0, total_mib: 0, holders: [], error: "off" }], LABELS);
    expect(row).toMatchObject({ color: "red", text: "weg", detail: "off", title: "off", action: null });
  });
});

describe("summaryColor and formatRemaining", () => {
  it("the closed bar shows the worst row", () => {
    expect(summaryColor(serviceRows(SNAPSHOT, LABELS).describer)).toBe("yellow");
    expect(summaryColor([])).toBe("grey");
  });

  it("days and hours only when there are some", () => {
    expect(formatRemaining(75, "T")).toBe("1:15");
    expect(formatRemaining(3 * 3600 + 15 * 60 + 20, "T")).toBe("3:15:20");
    expect(formatRemaining(2 * 86400 + 3 * 3600 + 15 * 60 + 20, "T")).toBe("2 T 03:15:20");
    expect(formatRemaining(-5, "T")).toBe("0:00");
  });
});

describe("localHoldRow", () => {
  const LOCAL = {
    lang: "de", hostUsed: "{used} von {total} MiB belegt", free: "keine Anfrage", requested: "{owner} möchte die Karten", granted: "überlassen an {owner}, wartet auf das Ende laufender Arbeit",
    active: "gehalten von {owner}", reason: "„{reason}“", model: "Modell {model}", since: "seit {time}",
    off: "GPU-Orc ist aus", unreachable: "GPU-Orc antwortet nicht: {error}", notRequested: "keine Anfrage", noHold: "kein Hold",
  };
  const SINCE = "2026-11-20T09:30:00+01:00";
  const time = new Date(SINCE).toLocaleTimeString("de", { hour: "2-digit", minute: "2-digit" });
  const status = (state: LocalHoldStatus["state"], extra: Partial<LocalHoldStatus> = {}): LocalHoldStatus => ({
    state, machine: "Mini", owner: "vllm-research", reason: "v13", since: SINCE, model: null, granted_by: null, error: "",
    used_mib: 120384, total_mib: 196608, ...extra,
  });
  const enabled = (row: ReturnType<typeof localHoldRow>) => [!row.grant.blocked, !row.end.blocked];

  it("free: a plain green line with how full the cards are and that nobody asks, nothing to grant or end", () => {
    const row = localHoldRow(status("free", { owner: null, reason: null, since: null }), LOCAL);
    // No hold is not empty cards: the title says how full they are.
    expect(row).toMatchObject({ label: "Mini", color: "green", title: "120.384 von 196.608 MiB belegt", detail: "keine Anfrage", card: false });
    expect(enabled(row)).toEqual([false, false]);
  });

  it("requested: an amber card, pulsing, the title names the owner, the detail reason, model and clock time", () => {
    const row = localHoldRow(status("requested", { model: "Big-27B" }), LOCAL);
    expect(row).toMatchObject({ color: "amber", pending: true, card: true, title: "vllm-research möchte die Karten", detail: `„v13“ · Modell Big-27B · seit ${time}` });
    expect(enabled(row)).toEqual([true, true]); // ending: the way out of a request whose asker crashed
    expect(row.grant).toMatchObject({ verb: "grant", item: "vllm-research" });
  });

  it("requested without a model: the detail has no model part", () => {
    expect(localHoldRow(status("requested"), LOCAL).detail).toBe(`„v13“ · seit ${time}`);
  });

  it("granted: a still card, waits for running work, only ending is enabled", () => {
    const row = localHoldRow(status("granted", { granted_by: "ki" }), LOCAL);
    expect(row).toMatchObject({ color: "amber", pending: false, card: true, title: "überlassen an vllm-research, wartet auf das Ende laufender Arbeit", detail: null });
    expect(enabled(row)).toEqual([false, true]);
  });

  it("active: a still card with the reason and the time, only ending is enabled", () => {
    const row = localHoldRow(status("active"), LOCAL);
    expect(row).toMatchObject({ color: "amber", pending: false, card: true, title: "gehalten von vllm-research", detail: `„v13“ · seit ${time}` });
    expect(enabled(row)).toEqual([false, true]);
    expect(row.end).toMatchObject({ verb: "end", item: "vllm-research" });
  });

  it("off is grey and unreachable red with the error, plain lines, both buttons greyed out", () => {
    const off = localHoldRow(status("off", { owner: null }), LOCAL);
    expect(off).toMatchObject({ color: "grey", title: "GPU-Orc ist aus", card: false });
    const down = localHoldRow(status("unreachable", { owner: null, error: "refused" }), LOCAL);
    expect(down).toMatchObject({ color: "red", title: "GPU-Orc antwortet nicht: refused", card: false });
    expect([...enabled(off), ...enabled(down)]).toEqual([false, false, false, false]);
  });
});
