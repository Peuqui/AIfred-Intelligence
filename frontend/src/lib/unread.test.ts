// Which answers are unread and where the "read up to here" line stands; which clicks on a bubble count as reading it.
import { expect, test } from "vitest";
import type { ChatEntry } from "../api/types";
import { isPlainClick, isUnread, lineAfterIndex, unreadCount } from "./unread";

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

test("the line stands under the last bubble read, above the first one when nothing is read, nowhere without unread", () => {
  expect(lineAfterIndex(history, "")).toBe(-1);
  expect(lineAfterIndex(history, "2026-11-21T10:00:05")).toBe(1);
  expect(lineAfterIndex(history, "2026-11-21T10:01:00")).toBe(2);
  expect(lineAfterIndex(history, "2026-11-21T10:01:09")).toBeNull();
  expect(lineAfterIndex([entry("user", "2026-11-21T10:00:00")], "")).toBeNull();
});

function clickOn(html: string, selector: string): MouseEvent {
  document.body.innerHTML = html;
  let seen: MouseEvent | null = null;
  document.body.addEventListener("click", (event) => (seen = event), { once: true });
  document.querySelector<HTMLElement>(selector)!.click();
  return seen!;
}

test("a click on text counts, a click on a button, link, picture, input or the head of a collapsible does not", () => {
  const body = '<article><p id="t">Text</p><button id="b">x</button><a id="a" href="#">l</a><img id="i" alt=""><input id="n"><details><summary id="s"><b id="sb">Denken</b></summary></details></article>';
  expect(isPlainClick(clickOn(body, "#t"))).toBe(true);
  for (const id of ["#b", "#a", "#i", "#n", "#s", "#sb"]) expect(isPlainClick(clickOn(body, id)), id).toBe(false);
});

test("a click while text is selected does not count", () => {
  document.body.innerHTML = "<article><p id='t'>Ein Satz</p></article>";
  const text = document.querySelector("#t")!;
  window.getSelection()!.selectAllChildren(text);
  let seen: MouseEvent | null = null;
  document.body.addEventListener("click", (event) => (seen = event), { once: true });
  (text as HTMLElement).click();
  expect(isPlainClick(seen!)).toBe(false);
  window.getSelection()!.removeAllRanges();
});
