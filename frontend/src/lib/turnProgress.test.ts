// The line the chat shows while a turn works without text (lib/turnProgress.ts). Without loaded texts t() gives the key.
import { expect, test } from "vitest";
import { progressLine } from "./turnProgress";

const phase = (name: string, current = 0, total = 0, failed = 0) => ({ phase: name, current, total, failed });

test("no turn, no line", () => {
  expect(progressLine(false, null, true)).toBeNull();
  expect(progressLine(false, phase("intent"), true)).toBeNull();
});

test("a reported phase shows, also inside a bubble that has text", () => {
  expect(progressLine(true, phase("intent"), true)).toMatchObject({ icon: "🤖", text: "automatic_decision", count: "" });
  expect(progressLine(true, phase("compress"), false)).toMatchObject({ text: "compressing_context" });
});

test("scraping counts the pages read and the unreachable ones; the bar covers both", () => {
  const line = progressLine(true, phase("scraping", 3, 8, 1), false);
  expect(line).toMatchObject({ text: "web_scraping", count: "3/8", failed: "websites_unreachable", done: 0.5 });
  expect(progressLine(true, phase("scraping", 3, 8, 2), false)?.failed).toBe("websites_unreachable_plural");
  expect(progressLine(true, phase("scraping", 0, 8, 0), false)?.failed).toBe("");
});

test("without a phase the turn generates its answer until text or a tool line comes", () => {
  expect(progressLine(true, null, true)).toMatchObject({ icon: "🧠", text: "generating_answer" });
  expect(progressLine(true, null, false)).toBeNull();
});

test("a phase the page does not know is an error, not an empty line", () => {
  expect(() => progressLine(true, phase("unheard_of"), true)).toThrow("unheard_of");
});
