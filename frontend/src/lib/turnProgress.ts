// The line the chat shows while a turn works and no text has come yet (until 08.11.2026 the Reflex banner): the phase
// the server reports (event turn_phase, lib/debug_bus.report_phase), else "generating the answer" until the first
// text or tool line arrives. tests/test_turn_phases.py checks that every phase the server reports has its entry here.
import { language, t, tCount } from "../i18n";
import { type Action, clockTime } from "./serviceStatus";

// The hold a turn waits for (lib/gpu_hold._hold_detail): whose cards, who holds them and why, active or granted, and
// whether it is this machine's own GPU-Orc (only that one can be ended from here).
export interface GpuHoldDetail {
  machine: string;
  owner: string;
  reason: string;
  since: string;
  state: "active" | "granted";
  local: boolean;
}

export interface TurnPhase {
  phase: string;
  detail?: GpuHoldDetail | null;
  current: number;   // pages read (scraping)
  total: number;     // pages to read; 0: the phase has no count
  failed: number;    // pages unreachable
}

export interface ProgressLine {
  icon: string;
  text: string;
  done: number;      // share of the pages done, 0 to 1 (the bar, shown with a count)
  count: string;     // "3/7", empty when the phase has no count
  failed: string;    // "1 website unreachable", empty when none failed
  // Only the wait for the cards: the button that ends the hold, greyed out (``blocked``, shown as text) when it may not.
  end?: { action: Action; reason: string };
}

const PHASES: Record<string, { icon: string; text: (phase: TurnPhase) => string }> = {
  intent: { icon: "🤖", text: () => t("automatic_decision") },
  images: { icon: "📷", text: () => t("describing_images") },
  compress: { icon: "🗜️", text: () => t("compressing_context") },
  model_wait: { icon: "⏳", text: () => t("waiting_for_model") },
  gpu_hold: { icon: "⏸️", text: (phase) => holdText(holdDetail(phase)) },
  scraping: { icon: "🔍", text: () => t("web_scraping") },
};

function holdDetail(phase: TurnPhase): GpuHoldDetail {
  if (!phase.detail) throw new Error("The phase gpu_hold came without the hold's detail");
  return phase.detail;
}

function holdText(hold: GpuHoldDetail): string {
  return t(`waiting_for_gpu_hold_${hold.state}`, {
    machine: hold.machine, owner: hold.owner, reason: hold.reason, time: clockTime(hold.since, language.value),
  });
}

// The button that ends the hold is for the own machine's GPU-Orc (active or granted alike); otherwise it says why not.
function holdEnd(hold: GpuHoldDetail): { action: Action; reason: string } {
  const blocked = hold.local ? undefined : t("waiting_for_gpu_hold_only_local", { machine: hold.machine });
  return { action: { verb: "end", service: "host", item: hold.owner, ...(blocked ? { blocked } : {}) }, reason: hold.reason };
}

// waitingForText: the turn shows nothing of its own yet (no streaming bubble, or one without text and tool line).
export function progressLine(running: boolean, phase: TurnPhase | null, waitingForText: boolean): ProgressLine | null {
  if (!running) return null;
  if (phase) {
    const known = PHASES[phase.phase];
    if (!known) throw new Error(`Unknown turn phase: ${phase.phase}`);
    const counted = phase.total > 0;
    return {
      icon: known.icon,
      text: known.text(phase),
      done: counted ? (phase.current + phase.failed) / phase.total : 0,
      count: counted ? `${phase.current}/${phase.total}` : "",
      failed: phase.failed > 0 ? tCount("websites_unreachable", phase.failed) : "",
      ...(phase.phase === "gpu_hold" ? { end: holdEnd(holdDetail(phase)) } : {}),
    };
  }
  if (!waitingForText) return null;
  return { icon: "✨", text: t("generating_answer"), done: 0, count: "", failed: "" };
}
