// Preferences of this device, kept in its browser (another screen may want them otherwise): whether Enter sends and
// how large the chat's text is. A browser that keeps nothing (private window, blocked storage) gets the default.
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

// The chat's text size in percent, in steps between the bounds (as the Reflex page had it: 80 to 200, by 10).
export const CHAT_ZOOM = { min: 80, max: 200, step: 10, default: 100 } as const;
const isZoom = (value: unknown): value is number =>
  typeof value === "number" && value >= CHAT_ZOOM.min && value <= CHAT_ZOOM.max;

export const enterSends = stored("aifred-enter-sends", true, isBoolean);
export const chatZoom = stored("aifred-chat-zoom", CHAT_ZOOM.default, isZoom);

export function zoomBy(steps: number): void {
  chatZoom.value = Math.min(CHAT_ZOOM.max, Math.max(CHAT_ZOOM.min, chatZoom.value + steps * CHAT_ZOOM.step));
}
