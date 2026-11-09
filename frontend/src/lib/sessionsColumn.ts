// Where the saved chats stand: as a column at the left of the chat when the window is wide enough
// (config.py UI_SESSIONS_COLUMN_MIN_WINDOW_PX, through the settings) and this device keeps the column on (devicePrefs), else
// as the accordion above the chat. The page, the chat and the list ask here.
import { computed, ref } from "vue";
import { useSettings } from "../stores/settings";
import { sessionsColumn } from "./devicePrefs";

const windowWidth = ref(window.innerWidth);
window.addEventListener("resize", () => {
  windowWidth.value = window.innerWidth;
});

export function useSessionsColumn() {
  const settings = useSettings();
  // The window is wide enough: only then the switch shows.
  const possible = computed(() => !!settings.current && windowWidth.value >= settings.current.sessions_column_min_window_px);
  const shown = computed(() => possible.value && sessionsColumn.value);
  function toggle(): void {
    sessionsColumn.value = !sessionsColumn.value;
  }
  return { possible, shown, toggle };
}
