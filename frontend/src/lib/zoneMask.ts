// The zone mask of a camera as the editor paints it: a grid of cells, each one of the kinds below. The server keeps it
// as `cells`, one digit per cell, row by row (vision settings `zone_masks`, read by the watch in
// aifred/lib/vision_filters/zone_mask.py); `rows` follows the picture's shape for the chosen number of columns.

export const NOTHING = 0;
export const IGNORE = 1;      // motion here raises nothing
export const BLACKOUT = 2;    // the pixels are blacked out before anything stores or reads them
export const ONLY_HERE = 3;   // once there is one such cell, motion counts only in these
export type Cell = typeof NOTHING | typeof IGNORE | typeof BLACKOUT | typeof ONLY_HERE;

export interface Mask {
  cols: number;
  rows: number;
  cells: Cell[];   // row by row, cols × rows
}

const MIN_ROWS = 4;

export function emptyMask(cols: number, rows: number): Mask {
  return { cols, rows, cells: new Array<Cell>(cols * rows).fill(NOTHING) };
}

// The rows that keep the cells square for a picture of this shape (height / width).
export function rowsFor(cols: number, aspect: number): number {
  return Math.max(MIN_ROWS, Math.round(cols * aspect));
}

// The stored digits as a mask; a digit of no kind counts as nothing.
export function fromCells(cols: number, rows: number, cells: string): Mask {
  const mask = emptyMask(cols, rows);
  for (let index = 0; index < mask.cells.length; index++) {
    const value = Number(cells[index]);
    mask.cells[index] = value === IGNORE || value === BLACKOUT || value === ONLY_HERE ? value : NOTHING;
  }
  return mask;
}

export function toCells(mask: Mask): string {
  return mask.cells.join("");
}

export function isEmpty(mask: Mask): boolean {
  return mask.cells.every((cell) => cell === NOTHING);
}

// A round brush: every cell within `size - 1` cells of (col, row) takes `value` (size 1 is the one cell).
export function paint(mask: Mask, col: number, row: number, size: number, value: Cell): void {
  const radius = size - 1;
  for (let dr = -radius; dr <= radius; dr++) {
    for (let dc = -radius; dc <= radius; dc++) {
      const r = row + dr;
      const c = col + dc;
      if (dr * dr + dc * dc <= radius * radius && r >= 0 && r < mask.rows && c >= 0 && c < mask.cols) {
        mask.cells[r * mask.cols + c] = value;
      }
    }
  }
}

// Per cell, whether the watch looks there, as it does: a mask switched off covers nothing; with a cell to watch only,
// only those; else everything but the ignored and the blacked out ones.
export function looksAt(mask: Mask, active: boolean): boolean[] {
  if (!active) return mask.cells.map(() => true);
  const onlyHere = mask.cells.includes(ONLY_HERE);
  return mask.cells.map((cell) => (onlyHere ? cell === ONLY_HERE : cell === NOTHING));
}

// The stream burns live motion into the picture in saturated blue (api/vision.py, _MotionOverlay: BGR 255/40/0 at
// 60 %), so a moving pixel always has much more blue than red and green; the sky's blue (much green) does not.
const BLUE_OVER_RED = 70;
const BLUE_OVER_GREEN = 70;
const BLUE_AT_LEAST = 110;

// The share (percent) of the watched part of the picture that moves, from an RGBA frame of the stream as wide and
// high as the editor's canvas; every `step`-th pixel in both directions counts (the share stays the same).
export function motionShare(rgba: Uint8ClampedArray, width: number, height: number, mask: Mask, active: boolean, step = 2): number {
  const looked = looksAt(mask, active);
  const pixels = new DataView(rgba.buffer, rgba.byteOffset, rgba.byteLength);
  const cellWidth = width / mask.cols;
  const cellHeight = height / mask.rows;
  let observed = 0;
  let moving = 0;
  for (let y = 0; y < height; y += step) {
    const row = Math.min(mask.rows - 1, Math.floor(y / cellHeight));
    for (let x = 0; x < width; x += step) {
      const col = Math.min(mask.cols - 1, Math.floor(x / cellWidth));
      if (!looked[row * mask.cols + col]) continue;
      observed++;
      const at = (y * width + x) * 4;
      const red = pixels.getUint8(at);
      const green = pixels.getUint8(at + 1);
      const blue = pixels.getUint8(at + 2);
      if (blue - red > BLUE_OVER_RED && blue - green > BLUE_OVER_GREEN && blue > BLUE_AT_LEAST) moving++;
    }
  }
  return observed > 0 ? (moving / observed) * 100 : 0;
}
