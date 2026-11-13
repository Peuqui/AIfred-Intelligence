// The status line's rules (lib/serviceStatus.ts), as the Reflex page had them in assets/custom.js.
import { describe, expect, it } from "vitest";
import type { ServiceSnapshot } from "../api/types";
import { formatRemaining, hostRows, serviceRows, summaryColor } from "./serviceStatus";

const LABELS = {
  lang: "de", noModel: "kein Modell", noneLoaded: "nichts", loading: "lädt", busy: "arbeitet",
  unreachable: "weg", hostHeld: "freigehalten", hostFreeCards: "frei", hostUsed: "{used} von {total} MiB belegt",
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
    const [row] = hostRows([{ name: "Aragon", reachable: true, held: "vllm", used_mib: 1024, total_mib: 24576, holders: ["eyes"], error: "" }], LABELS);
    expect(row).toMatchObject({ color: "yellow", label: "Aragon", action: { verb: "release", service: "host", item: "Aragon" } });
    expect(row?.text).toBe("freigehalten · 1.024 von 24.576 MiB belegt");
    expect(row?.detail).toBe("eyes");
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
