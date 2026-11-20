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
  it("a held machine is yellow and offers the release", () => {
    const holders = [{ consumer: "llama-swap", item: "eyes" }, { consumer: "tts-suite", item: "voice" }];
    const [row] = hostRows([{ name: "Aragon", reachable: true, held: "vllm", hold_state: "active", hold_owner: "vllm-research", hold_is_ours: false, used_mib: 1024, total_mib: 24576, holders, error: "" }], LABELS);
    expect(row).toMatchObject({ color: "yellow", label: "Aragon", action: { verb: "release", service: "host", item: "Aragon" } });
    expect(row?.text).toBe("1.024 von 24.576 MiB belegt");
    expect(row?.detail).toBe("freigehalten von vllm-research: vllm\nllama-swap: eyes\ntts-suite: voice"); // visible without hover
    expect(row?.title).toBe("freigehalten von vllm-research: vllm");
    expect(row?.action?.blocked).toBe("gehalten von vllm-research"); // another owner's: greyed out
  });

  it("a requested hold is yellow too and says so in the title", () => {
    const [row] = hostRows([{ name: "Aragon", reachable: true, held: "vllm", hold_state: "requested", hold_owner: "aifred-statusleiste", hold_is_ours: true, used_mib: 1, total_mib: 2, holders: [], error: "" }], LABELS);
    expect(row).toMatchObject({ color: "yellow", title: "freigehalten von aifred-statusleiste (angefordert): vllm", action: { verb: "release" } });
    expect(row?.action?.blocked).toBeUndefined(); // the status bar's own: it may let go
    expect(row?.detail).toBe("freigehalten von aifred-statusleiste (angefordert): vllm");
  });

  it("a machine that is off is only a red dot", () => {
    const [row] = hostRows([{ name: "Aragon", reachable: false, held: null, hold_state: null, hold_owner: null, hold_is_ours: false, used_mib: 0, total_mib: 0, holders: [], error: "off" }], LABELS);
    expect(row).toMatchObject({ color: "red", text: "", title: "off", action: null });
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
    lang: "de", free: "keine Anfrage", requested: "{owner} möchte die Karten: {reason} (seit {time})",
    granted: "überlassen an {owner}, wartet auf das Ende laufender Arbeit", active: "gehalten von {owner}: {reason} (seit {time})",
    off: "GPU-Orc ist aus", unreachable: "GPU-Orc antwortet nicht: {error}", notRequested: "keine Anfrage", notActive: "kein Hold",
  };
  const SINCE = "2026-11-20T09:30:00+01:00";
  const status = (state: LocalHoldStatus["state"], extra: Partial<LocalHoldStatus> = {}): LocalHoldStatus => ({
    state, machine: "Mini", owner: "vllm-research", reason: "v13", since: SINCE, model: null, granted_by: null, error: "", ...extra,
  });
  const enabled = (row: ReturnType<typeof localHoldRow>) => [!row.grant.blocked, !row.end.blocked];

  it("free: green, nothing to grant or end", () => {
    const row = localHoldRow(status("free", { owner: null, reason: null, since: null }), LOCAL);
    expect(row).toMatchObject({ label: "Mini", color: "green", text: "keine Anfrage" });
    expect(enabled(row)).toEqual([false, false]);
  });

  it("requested: yellow, names owner, reason and the clock time, only the grant is enabled", () => {
    const row = localHoldRow(status("requested"), LOCAL);
    const time = new Date(SINCE).toLocaleTimeString("de", { hour: "2-digit", minute: "2-digit" });
    expect(row).toMatchObject({ color: "yellow", text: `vllm-research möchte die Karten: v13 (seit ${time})` });
    expect(row.text).toMatch(/seit \d{2}:\d{2}\)$/);
    expect(enabled(row)).toEqual([true, false]);
    expect(row.grant).toMatchObject({ verb: "grant", item: "vllm-research" });
  });

  it("granted: yellow, waits for running work, both buttons greyed out", () => {
    const row = localHoldRow(status("granted", { granted_by: "ki" }), LOCAL);
    expect(row).toMatchObject({ color: "yellow", text: "überlassen an vllm-research, wartet auf das Ende laufender Arbeit" });
    expect(enabled(row)).toEqual([false, false]);
  });

  it("active: yellow, only ending is enabled", () => {
    const row = localHoldRow(status("active"), LOCAL);
    expect(row.text).toMatch(/^gehalten von vllm-research: v13 \(seit \d{2}:\d{2}\)$/);
    expect(enabled(row)).toEqual([false, true]);
    expect(row.end).toMatchObject({ verb: "end", item: "vllm-research" });
  });

  it("off is grey and unreachable red with the error, both buttons greyed out", () => {
    const off = localHoldRow(status("off", { owner: null }), LOCAL);
    expect(off).toMatchObject({ color: "grey", text: "GPU-Orc ist aus" });
    const down = localHoldRow(status("unreachable", { owner: null, error: "refused" }), LOCAL);
    expect(down).toMatchObject({ color: "red", text: "GPU-Orc antwortet nicht: refused" });
    expect([...enabled(off), ...enabled(down)]).toEqual([false, false, false, false]);
  });
});
