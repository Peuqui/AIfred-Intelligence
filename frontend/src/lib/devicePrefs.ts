// Preferences of this device, kept in its browser (another screen may want them otherwise): whether Enter sends,
// whether a question sticks at the top, where the saved chats stand, and three sizes — the chat's text, the interface (controls and labels) and the console. A browser that keeps nothing
// (private window, blocked storage) gets the default.
import { type Ref, ref, watch } from "vue";

function stored<T>(key: string, fallback: T, valid: (value: unknown) => value is T): Ref<T> {
  let initial = fallback;
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) ?? "null");
    if (valid(saved)) initial = saved;
  } catch {
    // nothing kept on this device: the default
  }
  const value = ref(initial) as Ref<T>;
  watch(value, (next) => {
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // the browser keeps nothing: it holds for this page only
    }
  });
  return value;
}

const isBoolean = (value: unknown): value is boolean => typeof value === "boolean";

// A size in percent, in steps between the bounds (as the Reflex page had it for the chat: 80 to 200, by 10).
export const ZOOM = { min: 80, max: 200, step: 10, default: 100 } as const;
const isZoom = (value: unknown): value is number => typeof value === "number" && value >= ZOOM.min && value <= ZOOM.max;

export const enterSends = stored("aifred-enter-sends", true, isBoolean);
// A narrow screen as the interface's CSS counts it (@media (max-width: 760px)): some defaults differ on a phone.
const NARROW_SCREEN = window.matchMedia("(max-width: 760px)").matches;
// The question of a turn sticks at the top of the chat while its answer scrolls; on a phone it took too much of the
// screen, so there it starts off (Peuqui 09.11.2026).
export const pinQuestion = stored("aifred-pin-question", !NARROW_SCREEN, isBoolean);
// The saved chats as a column at the left of the chat on a wide window (lib/sessionsColumn.ts), or above it.
export const sessionsColumn = stored("aifred-sessions-column", true, isBoolean);
export const chatZoom = stored("aifred-chat-zoom", ZOOM.default, isZoom);
export const uiZoom = stored("aifred-ui-zoom", ZOOM.default, isZoom);
export const consoleZoom = stored("aifred-console-zoom", ZOOM.default, isZoom);

// The heights the user dragged with the grips under the chat, the input field and the console (px in the box's own
// units); none = the default height.
const isHeight = (value: unknown): value is number | null => value === null || (typeof value === "number" && value > 0);
export const chatHeight = stored<number | null>("aifred-chat-height", null, isHeight);
export const inputHeight = stored<number | null>("aifred-input-height", null, isHeight);
export const consoleHeight = stored<number | null>("aifred-console-height", null, isHeight);

const zooms = { chat: chatZoom, ui: uiZoom, console: consoleZoom };
export type ZoomName = keyof typeof zooms;

/** Every size of this device back to the default (the chat's, the interface's, the console's, and the dragged heights). */
export function resetZooms(): void {
  for (const zoom of Object.values(zooms)) zoom.value = ZOOM.default;
  for (const height of [chatHeight, inputHeight, consoleHeight]) height.value = null;
}

export function zoomBy(name: ZoomName, steps: number): void {
  const zoom = zooms[name];
  zoom.value = Math.min(ZOOM.max, Math.max(ZOOM.min, zoom.value + steps * ZOOM.step));
}
