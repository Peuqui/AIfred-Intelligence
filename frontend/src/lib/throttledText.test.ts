import { effectScope, ref } from "vue";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { throttledText } from "./throttledText";

beforeEach(() => void vi.useFakeTimers());
afterEach(() => void vi.useRealTimers());

function follow(text: { value: string }): { value: string } {
  let shown = { value: "" };
  effectScope().run(() => { shown = throttledText(() => text.value, 200); });
  return shown;
}

test("the first piece is shown at once, the pieces after it together when the interval is over", () => {
  const text = ref("");
  const shown = follow(text);
  text.value = "a";
  expect(shown.value).toBe("a");
  text.value = "ab";
  text.value = "abc";
  expect(shown.value).toBe("a");
  vi.advanceTimersByTime(200);
  expect(shown.value).toBe("abc");
  text.value = "abcd";
  expect(shown.value).toBe("abcd");   // a new interval begins with the next piece
});

test("a text that is not a continuation is shown at once", () => {
  const text = ref("alte Antwort");
  const shown = follow(text);
  text.value = "alte Antwort mehr";
  text.value = "neue";
  expect(shown.value).toBe("neue");
  text.value = "";
  expect(shown.value).toBe("");
});
