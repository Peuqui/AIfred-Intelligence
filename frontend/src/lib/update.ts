// A newly installed build (api/client.ts: newVersion) is loaded at once when nothing would be lost; the page shows that
// before it reloads, and once more after, so that the reader sees the new version is there.
const UPDATED_MARK = "aifred-updated";
// How long the bar "loading" is shown before the page reloads (long enough to be read).
export const UPDATE_BANNER_MS = 1500;
// How long "loaded" stays after the reload.
export const UPDATED_NOTICE_MS = 4000;

export function reloadForUpdate(): void {
  try {
    sessionStorage.setItem(UPDATED_MARK, "1");
  } catch {
    // the browser keeps nothing: the reload still happens, only its confirmation does not show
  }
  window.location.reload();
}

/** Whether this page load is the reload after an update; the mark is used up. */
export function takeUpdatedMark(): boolean {
  try {
    const marked = sessionStorage.getItem(UPDATED_MARK) !== null;
    sessionStorage.removeItem(UPDATED_MARK);
    return marked;
  } catch {
    return false;
  }
}
