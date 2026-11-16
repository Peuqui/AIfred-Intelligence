// The beginning of a long text: at most `maxLines` lines and `maxChars` characters, an ellipsis where it was cut.
// A text that fits is returned whole (trimmed).
export function clip(text: string, maxChars: number, maxLines: number): string {
  const trimmed = text.trim();
  let shown = trimmed.split("\n").slice(0, maxLines).join("\n");
  if (shown.length > maxChars) shown = shown.slice(0, maxChars).trimEnd();
  return shown.length < trimmed.length ? `${shown}…` : trimmed;
}
