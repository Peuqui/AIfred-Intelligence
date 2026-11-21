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

// Parts of a bubble that do something on their own click (and the selection of text): a click there is not "I read
// this far". Pictures open the large view.
const OWN_CLICK = "button, a, input, select, textarea, video, audio, summary, img";

export function isPlainClick(event: MouseEvent): boolean {
  if (event.target instanceof Element && event.target.closest(OWN_CLICK)) return false;
  return window.getSelection()?.isCollapsed !== false;
}
