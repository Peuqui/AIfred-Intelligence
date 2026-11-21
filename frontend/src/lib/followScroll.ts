// Where a scrolling box goes when something happens, and whether it follows what is added at its end. One place for the
// rules and their numbers; the chat's movement itself is in lib/useChatScroll.ts. The numbers and the rules are
// Agent-Orc's (feedScroll.ts), so both apps feel the same.
//
// The reader is either FOLLOWING (stands at the end) or READING (has scrolled up). That is decided by the reader's own
// scrolling alone, never by a measurement before each change:
//   - Following: what is new comes into view; with something unread the box moves on until the "read up to here" line
//     stands at the upper edge, and stays there (never back up); with nothing unread it goes to the end.
//   - Reading: nothing moves; a button says what came in below.
// Opening a chat: to the line when something is unread; else to where the reader was; else the end.

/** The reader is following within this distance (px) of the end of the box. */
export const FOLLOW_WITHIN_PX = 80;
/** The room kept above what is read from its top (a line, a bubble) at the upper edge of the box. */
export const TOP_ROOM_PX = 12;

/** What happened: `opened` a chat was shown, `own` the reader sent something, `incoming` the chat grew (a bubble, an answer being written). */
export type Reason = "opened" | "own" | "incoming";
export type Move = "end" | "oldestUnread" | "remembered" | "stay";

export interface Situation {
  following: boolean;
  hasUnread: boolean;
  hasRemembered: boolean;
}

export function isFollowing(distanceFromEnd: number): boolean {
  return distanceFromEnd <= FOLLOW_WITHIN_PX;
}

export function isAtEnd(box: HTMLElement): boolean {
  return isFollowing(box.scrollHeight - box.scrollTop - box.clientHeight);
}

export function moveFor(reason: Reason, situation: Situation): Move {
  if (reason === "opened") {
    if (situation.hasUnread) return "oldestUnread";
    return situation.hasRemembered ? "remembered" : "end";
  }
  if (reason === "own") return "end";
  if (!situation.following) return "stay";
  return situation.hasUnread ? "oldestUnread" : "end";
}
