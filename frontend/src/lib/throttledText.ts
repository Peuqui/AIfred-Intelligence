// A text that grows piece by piece (an answer being written), followed at most once per interval: the first piece at
// once, the pieces after it together when the interval is over. A value that is not a continuation of what is shown
// (a new answer, nothing) is shown at once, so a new answer does not start with the old one.
import { type Ref, ref, watch } from "vue";

export function throttledText(source: () => string, intervalMs: number): Ref<string> {
  const shown = ref(source());
  let timer: ReturnType<typeof setTimeout> | null = null;
  watch(
    source,
    (text) => {
      if (!text.startsWith(shown.value)) shown.value = text;
      if (timer !== null) return;
      shown.value = text;
      timer = setTimeout(() => {
        timer = null;
        shown.value = source();
      }, intervalMs);
    },
    { flush: "sync" },
  );
  return shown;
}
