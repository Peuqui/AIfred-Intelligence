// Which answers of a chat are still unread, and where the "read up to here" line stands. The server keeps the mark per
// session (read_until: the timestamp of the last bubble read); it only moves forward. Timestamps are ISO strings, which
// compare as text.
import type { ChatEntry } from "../api/types";

// Only an answer can be unread: the user's own messages never are.
export function isUnread(entry: ChatEntry, readUntil: string): boolean {
  return entry.role === "assistant" && entry.timestamp > readUntil;
}

export function unreadCount(history: ChatEntry[], readUntil: string): number {
  return history.filter((entry) => isUnread(entry, readUntil)).length;
}

// The line stands directly under the last bubble read, above everything newer: the index of that bubble, -1 for above the
// first one (nothing read yet), null when nothing below it is unread (no line).
export function lineAfterIndex(history: ChatEntry[], readUntil: string): number | null {
  const firstUnread = history.findIndex((entry) => isUnread(entry, readUntil));
  if (firstUnread < 0) return null;
  let line = -1;
  for (let at = 0; at < firstUnread; at += 1) {
    if (history[at]!.timestamp <= readUntil) line = at;
  }
  return line;
}

// Parts of a bubble that do something on their own click: a click there is not "I read this far". Pictures open the
// large view.
const OWN_CLICK = "button, a, input, select, textarea, video, audio, summary, img";

// How far the pointer may move between press and release and still be a click; more is selecting text by dragging.
// Not the selection itself: a click into text that is already selected still sees it, and was swallowed (21.11.2026).
export const CLICK_MOVE_TOLERANCE_PX = 4;

export interface Press {
  x: number;
  y: number;
}

/** A click that says "read up to here": not on a part with a click of its own, and not the end of a drag from `press`
 * (where the pointer went down; null when the click came without a press, e.g. from the keyboard). */
export function isPlainClick(event: MouseEvent, press: Press | null): boolean {
  if (event.target instanceof Element && event.target.closest(OWN_CLICK)) return false;
  return press === null || Math.hypot(event.clientX - press.x, event.clientY - press.y) <= CLICK_MOVE_TOLERANCE_PX;
}
