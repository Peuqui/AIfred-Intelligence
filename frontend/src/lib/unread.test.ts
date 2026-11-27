// Which answers are unread and where the "read up to here" line stands; which clicks on a bubble count as reading it.
import { expect, test } from "vitest";
import type { ChatEntry } from "../api/types";
import { isPlainClick, isUnread, lineBeforeIndex, unreadCount } from "./unread";

function entry(role: "user" | "assistant", timestamp: string): ChatEntry {
  return { role, timestamp, content: "x", agent: "", agent_display_name: "", agent_emoji: "", mode: "", time_display: "", has_audio: false, tts_note: "" };
}
const history = [entry("user", "2026-11-21T10:00:00"), entry("assistant", "2026-11-21T10:00:05"), entry("user", "2026-11-21T10:01:00"), entry("assistant", "2026-11-21T10:01:09")];

test("only an answer after the mark is unread, the user's own message never", () => {
  expect(isUnread(history[0]!, "")).toBe(false);
  expect(isUnread(history[1]!, "")).toBe(true);
  expect(isUnread(history[1]!, "2026-11-21T10:00:05")).toBe(false);
  expect(isUnread(history[3]!, "2026-11-21T10:00:05")).toBe(true);
  expect(unreadCount(history, "")).toBe(2);
  expect(unreadCount(history, "2026-11-21T10:00:05")).toBe(1);
  expect(unreadCount(history, "2026-11-21T10:01:09")).toBe(0);
});

test("the line stands directly above the first unread answer, nowhere without unread", () => {
  expect(lineBeforeIndex(history, "")).toBe(1);
  expect(lineBeforeIndex(history, "2026-11-21T10:00:05")).toBe(3);
  expect(lineBeforeIndex(history, "2026-11-21T10:01:09")).toBeNull();
  expect(lineBeforeIndex([entry("user", "2026-11-21T10:00:00")], "")).toBeNull();
});

test("between a question and its first answer: a long question of a job is passed over", () => {
  // The mark at the question itself (e.g. a job's text, read in the list before its answer came).
  expect(lineBeforeIndex(history, "2026-11-21T10:01:00")).toBe(3);
});

test("within a turn (a debate), after an answer read: the line stands between the answers", () => {
  const debate = [entry("user", "2026-11-21T10:00:00"), entry("assistant", "2026-11-21T10:00:05"), entry("assistant", "2026-11-21T10:00:09")];
  expect(lineBeforeIndex(debate, "2026-11-21T10:00:05")).toBe(2);
});

function clickOn(html: string, selector: string): MouseEvent {
  document.body.innerHTML = html;
  let seen: MouseEvent | null = null;
  document.body.addEventListener("click", (event) => (seen = event), { once: true });
  document.querySelector<HTMLElement>(selector)!.click();
  return seen!;
}

test("a click counts wherever on the bubble: on text, a button, link, picture, input or the head of a collapsible", () => {
  const body = '<article><p id="t">Text</p><button id="b">x</button><a id="a" href="#">l</a><img id="i" alt=""><input id="n"><details><summary id="s"><b id="sb">Denken</b></summary></details></article>';
  for (const id of ["#t", "#b", "#a", "#i", "#n", "#s", "#sb"]) expect(isPlainClick(clickOn(body, id), null), id).toBe(true);
});

test("a click into text that is already selected counts; the end of dragging to select text does not", () => {
  document.body.innerHTML = "<article><p id='t'>Ein Satz</p></article>";
  const text = document.querySelector<HTMLElement>("#t")!;
  window.getSelection()!.selectAllChildren(text);
  const at = (x: number, y: number): MouseEvent => {
    let seen: MouseEvent | null = null;
    document.body.addEventListener("click", (event) => (seen = event), { once: true });
    text.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: x, clientY: y }));
    return seen!;
  };
  expect(isPlainClick(at(100, 50), { x: 100, y: 50 })).toBe(true);
  expect(isPlainClick(at(103, 52), { x: 100, y: 50 })).toBe(true);
  expect(isPlainClick(at(160, 50), { x: 100, y: 50 })).toBe(false);
  window.getSelection()!.removeAllRanges();
});

test("a request of someone else (a job's task) is never unread: the line stands above its answer", () => {
  const job = { ...entry("user", "2026-11-23T19:00:56"), agent: "scheduler", agent_display_name: "Horarium · Deal-Check" };
  const answer = entry("assistant", "2026-11-23T19:17:01");
  expect(isUnread(job, "")).toBe(false);
  expect(lineBeforeIndex([job, answer], "")).toBe(1);
});
