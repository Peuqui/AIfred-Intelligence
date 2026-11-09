// Whether a scrolling box (the chat's history, the console) follows what is added at its end: only while the reader
// stands at the end. Scrolled up to read, the box stays where it is until they come back down; no switch (the Reflex
// page had one). The distance is Agent-Orc's (feedScroll.ts), so both apps feel the same.
export const FOLLOW_WITHIN_PX = 80;

export function isAtEnd(box: HTMLElement): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= FOLLOW_WITHIN_PX;
}

// Room above an answer's start when the box stops there (Agent-Orc's TOP_ROOM_PX).
export const TOP_ROOM_PX = 12;

/** Where a box that follows its end goes: the end, but not past where the newest ``answer`` begins at the upper edge
 * (below the question ``stuck`` there), so a long answer is read from its start, as in Agent-Orc (useFeedScroll's
 * toOldestUnread). Never up. */
export function followTop(box: HTMLElement, answer: HTMLElement | null, stuck: HTMLElement | null): number {
  const end = box.scrollHeight - box.clientHeight;
  if (!answer) return end;
  const above = answer.getBoundingClientRect().top - box.getBoundingClientRect().top;
  const start = box.scrollTop + above - (stuck?.getBoundingClientRect().height ?? 0) - TOP_ROOM_PX;
  return Math.max(box.scrollTop, Math.min(end, start));
}
