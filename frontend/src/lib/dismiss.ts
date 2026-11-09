// A small window that opens beside its button (the dictation's settings, the vision's newest events) closes like a
// dialog (ModalDialog): a click or tap beside it, or Escape. The button lies inside ``box``, so it still toggles.
import { type Ref, onBeforeUnmount, watch } from "vue";

export function closeWhenBeside(open: Ref<boolean>, box: Ref<HTMLElement | null>): void {
  function pointer(event: PointerEvent): void {
    if (!box.value?.contains(event.target as Node)) open.value = false;
  }
  function key(event: KeyboardEvent): void {
    if (event.key === "Escape") open.value = false;
  }
  function listen(on: boolean): void {
    if (on) {
      document.addEventListener("pointerdown", pointer, true);
      document.addEventListener("keydown", key);
    } else {
      document.removeEventListener("pointerdown", pointer, true);
      document.removeEventListener("keydown", key);
    }
  }
  watch(open, listen);
  onBeforeUnmount(() => listen(false));
}
