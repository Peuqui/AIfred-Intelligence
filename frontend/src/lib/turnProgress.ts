// The line the chat shows while a turn works and no text has come yet (until 08.11.2026 the Reflex banner): the phase
// the server reports (event turn_phase, lib/debug_bus.report_phase), else "generating the answer" until the first
// text or tool line arrives. tests/test_turn_phases.py checks that every phase the server reports has its entry here.
import { t, tCount } from "../i18n";

export interface TurnPhase {
  phase: string;
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
}

const PHASES: Record<string, { icon: string; text: () => string }> = {
  intent: { icon: "🤖", text: () => t("automatic_decision") },
  images: { icon: "📷", text: () => t("describing_images") },
  compress: { icon: "🗜️", text: () => t("compressing_context") },
  scraping: { icon: "🔍", text: () => t("web_scraping") },
};

// waitingForText: the turn shows nothing of its own yet (no streaming bubble, or one without text and tool line).
export function progressLine(running: boolean, phase: TurnPhase | null, waitingForText: boolean): ProgressLine | null {
  if (!running) return null;
  if (phase) {
    const known = PHASES[phase.phase];
    if (!known) throw new Error(`Unknown turn phase: ${phase.phase}`);
    const counted = phase.total > 0;
    return {
      icon: known.icon,
      text: known.text(),
      done: counted ? (phase.current + phase.failed) / phase.total : 0,
      count: counted ? `${phase.current}/${phase.total}` : "",
      failed: phase.failed > 0 ? tCount("websites_unreachable", phase.failed) : "",
    };
  }
  if (!waitingForText) return null;
  return { icon: "✨", text: t("generating_answer"), done: 0, count: "", failed: "" };
}
