// Where the reader last saw a chat, kept per session in this browser (another device has its own place): the bubble that
// stood lowest in the box, and how far its lower edge was above the box's lower edge (px; negative: it reached beyond).
// A browser that keeps nothing (private window, blocked storage) has no place.

const KEY_PREFIX = "aifred-chat-place-";

export interface ChatPlace {
  key: string;
  offset: number;
}

const isPlace = (value: unknown): value is ChatPlace =>
  typeof value === "object" && value !== null
  && typeof (value as ChatPlace).key === "string" && typeof (value as ChatPlace).offset === "number";

export function readPlace(sessionId: string): ChatPlace | null {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(KEY_PREFIX + sessionId) ?? "null");
    return isPlace(saved) ? saved : null;
  } catch {
    return null;
  }
}

export function savePlace(sessionId: string, place: ChatPlace): void {
  try {
    localStorage.setItem(KEY_PREFIX + sessionId, JSON.stringify(place));
  } catch {
    // the browser keeps nothing: the chat opens at its end next time
  }
}
