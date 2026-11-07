// The box of the crop dialog, in percent of the image from its top left, as the server's crop takes it.
// A grip is the edges it moves: "n", "e", "s", "w" or a corner ("ne", …); "" is the box itself, which
// moves as a whole. The box stays inside the image and keeps at least MIN_SIDE on each side.

export type Box = { x: number; y: number; width: number; height: number };
export type Grip = "" | "n" | "s" | "w" | "e" | "nw" | "ne" | "sw" | "se";

export const GRIPS: Grip[] = ["nw", "ne", "sw", "se", "n", "s", "w", "e"];
export const WHOLE: Box = { x: 0, y: 0, width: 100, height: 100 };
export const MIN_SIDE = 10;

const clamp = (value: number, low: number, high: number): number => Math.max(low, Math.min(high, value));

// The box after its grip was pulled by dx, dy (percent of the image) from where the pull started.
export function pulled(start: Box, grip: Grip, dx: number, dy: number): Box {
  if (grip === "") {
    return { ...start, x: clamp(start.x + dx, 0, 100 - start.width), y: clamp(start.y + dy, 0, 100 - start.height) };
  }
  const box = { ...start };
  const right = start.x + start.width;
  const bottom = start.y + start.height;
  if (grip.includes("w")) {
    box.x = clamp(start.x + dx, 0, right - MIN_SIDE);
    box.width = right - box.x;
  }
  if (grip.includes("e")) box.width = clamp(start.width + dx, MIN_SIDE, 100 - start.x);
  if (grip.includes("n")) {
    box.y = clamp(start.y + dy, 0, bottom - MIN_SIDE);
    box.height = bottom - box.y;
  }
  if (grip.includes("s")) box.height = clamp(start.height + dy, MIN_SIDE, 100 - start.y);
  return box;
}
