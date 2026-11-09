// Whether a scrolling box (the chat's history, the console) follows what is added at its end: only while the reader
// stands at the end. Scrolled up to read, the box stays where it is until they come back down; no switch (the Reflex
// page had one). The distance is Agent-Orc's (feedScroll.ts), so both apps feel the same.
export const FOLLOW_WITHIN_PX = 80;

export function isAtEnd(box: HTMLElement): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= FOLLOW_WITHIN_PX;
}


// Room above an answer's start when the box jumps there (Agent-Orc's TOP_ROOM_PX).
export const TOP_ROOM_PX = 12;

/** Where a finished ``answer`` begins at the upper edge of the box (below the question ``stuck`` there), or the end
 * when what follows it fits: an answer that streamed past the screen is read from its start. */
export function answerStartTop(box: HTMLElement, answer: HTMLElement, stuck: HTMLElement | null): number {
  const end = box.scrollHeight - box.clientHeight;
  const above = answer.getBoundingClientRect().top - box.getBoundingClientRect().top;
  return Math.min(end, box.scrollTop + above - (stuck?.getBoundingClientRect().height ?? 0) - TOP_ROOM_PX);
}
