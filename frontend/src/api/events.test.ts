// The event stream delivers only the kinds src/api/events.ts subscribes to: a kind the chat store
// handles but the list lacks never arrives (07.11.2026: the dictation's text never reached the input).
import { expect, test } from "vitest";
import store from "../stores/chat.ts?raw";
import { SESSION_EVENT_KINDS } from "./events";

test("every kind the chat store handles is subscribed", () => {
  const handled = [...store.matchAll(/case "([a-z_]+)":/g)].map((match) => match[1] ?? "");
  const subscribed: readonly string[] = SESSION_EVENT_KINDS;
  expect(handled.length).toBeGreaterThan(0);
  expect(handled.filter((kind) => !subscribed.includes(kind))).toEqual([]);
});
