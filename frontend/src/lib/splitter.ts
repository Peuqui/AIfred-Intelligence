// The movable divider between two columns (the documents page: file list | preview): the share of the width the left
// column takes, kept between bounds so that neither column can be dragged away (their minimum widths are CSS's).
export const SPLIT = { min: 0.2, max: 0.75, default: 0.4, keyStep: 0.02 } as const;

export const clampSplit = (share: number): number => Math.min(SPLIT.max, Math.max(SPLIT.min, share));

// The share at a pointer position: its distance from the left edge of the container over the container's width.
// A ratio, not pixels: the zoom of the interface scales both alike.
export function splitAt(pointerX: number, container: { left: number; width: number }): number {
  return clampSplit((pointerX - container.left) / container.width);
}
