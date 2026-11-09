// Whether a scrolling box (the chat's history, the console) follows what is added at its end: only while the reader
// stands at the end. Scrolled up to read, the box stays where it is until they come back down; no switch (the Reflex
// page had one). The distance is Agent-Orc's (feedScroll.ts), so both apps feel the same.
export const FOLLOW_WITHIN_PX = 80;

export function isAtEnd(box: HTMLElement): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= FOLLOW_WITHIN_PX;
}
