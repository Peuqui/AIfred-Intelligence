import { expect, test } from "vitest";
import { BLACKOUT, IGNORE, NOTHING, ONLY_HERE, emptyMask, fromCells, isEmpty, looksAt, motionShare, paint, rowsFor, toCells } from "./zoneMask";

test("the stored digits come back as they went, a digit of no kind is nothing", () => {
  const mask = fromCells(4, 2, "01230129");
  expect(mask.cells).toEqual([0, 1, 2, 3, 0, 1, 2, 0]);
  expect(toCells(mask)).toBe("01230120");
  expect(isEmpty(emptyMask(3, 3)) && !isEmpty(mask)).toBe(true);
});

test("the rows follow the picture's shape, never fewer than four", () => {
  expect(rowsFor(48, 9 / 16)).toBe(27);
  expect(rowsFor(16, 3 / 4)).toBe(12);
  expect(rowsFor(16, 0.1)).toBe(4);
});

test("the brush is round and stays inside the grid", () => {
  const mask = emptyMask(5, 5);
  paint(mask, 2, 2, 2, IGNORE);      // radius 1: the cell and its four neighbours, no corners
  expect(toCells(mask)).toBe("00000" + "00100" + "01110" + "00100" + "00000");
  paint(mask, 0, 0, 1, BLACKOUT);    // size 1: the one cell
  paint(mask, 4, 4, 2, ONLY_HERE);   // at the corner: what lies outside is left out
  expect(toCells(mask)).toBe("20000" + "00100" + "01110" + "00103" + "00033");
  paint(mask, 3, 3, 2, NOTHING);     // erasing
  expect(toCells(mask)).toBe("20000" + "00100" + "01100" + "00000" + "00003");
});

test("the watch looks where the server's watch looks", () => {
  expect(looksAt(fromCells(3, 1, "012"), true)).toEqual([true, false, false]);
  const onlyHere = fromCells(3, 1, "031");
  expect(looksAt(onlyHere, true)).toEqual([false, true, false]);
  expect(looksAt(onlyHere, false)).toEqual([true, true, true]);
});

function frame(width: number, height: number, blue: (x: number, y: number) => boolean): Uint8ClampedArray {
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const at = (y * width + x) * 4;
      // burned-in motion, else a sky-blue background (much green: no motion)
      rgba.set(blue(x, y) ? [100, 70, 230, 255] : [90, 160, 230, 255], at);
    }
  }
  return rgba;
}

test("the moving share counts burned-in blue in the watched cells only", () => {
  const rgba = frame(4, 2, (x) => x < 2);                  // the left half moves
  const mask = emptyMask(2, 1);
  expect(motionShare(rgba, 4, 2, mask, true, 1)).toBe(50);
  mask.cells[0] = IGNORE;                                   // the left half ignored: nothing moves where it looks
  expect(motionShare(rgba, 4, 2, mask, true, 1)).toBe(0);
  expect(motionShare(rgba, 4, 2, mask, false, 1)).toBe(50);   // switched off: it looks everywhere
  mask.cells[0] = ONLY_HERE;                                // only the left half
  expect(motionShare(rgba, 4, 2, mask, true, 1)).toBe(100);
  mask.cells[0] = BLACKOUT;
  mask.cells[1] = BLACKOUT;                                 // nothing left to look at
  expect(motionShare(rgba, 4, 2, mask, true, 1)).toBe(0);
});
