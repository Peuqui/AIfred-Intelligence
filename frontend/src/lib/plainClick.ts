// Whether a press of the pointer was one plain click or tap on an entry of the file browser (FileBrowser.vue), which
// then opens it: the primary button without Ctrl/Shift, short (a long press opens VueFinder's context menu), still (a
// frame drawn or a list pushed is no click), and not the second half of a double click on a folder just opened.
export const LONG_PRESS_MS = 500; // VueFinder's own long press
const STILL_PX = 10;
const AFTER_FOLDER_MS = 500;
// VueFinder selects a tapped entry 100 ms after the finger is up; the click is read after that.
export const CLICK_SETTLE_MS = 180;

export interface Press { x: number; y: number; at: number; plain: boolean }

export function plainClick(down: Press, up: { clientX: number; clientY: number; timeStamp: number }, folderOpenedAt: number): boolean {
  return down.plain
    && up.timeStamp - down.at < LONG_PRESS_MS
    && Math.hypot(up.clientX - down.x, up.clientY - down.y) < STILL_PX
    && down.at - folderOpenedAt > AFTER_FOLDER_MS;
}
