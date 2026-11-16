// The text of an answer that is still being written, as sections: the finished ones are rendered once and keep their
// HTML and their place in the page, only the last, open one is rendered again with every piece. Rendering the whole
// text with every piece costs as much as the text is long (320 ms for 300 kB of thinking), and the pieces come every
// 50 ms: an old tablet did nothing else any more (16.11.2026).
import { renderMarkdown } from "./markdown";

/** A piece of the text as it stands (`raw`, so that the pieces together are the text) and its HTML. */
export type StreamSection = { raw: string; html: string };

// A section ends at a blank line once it has this many characters: few nodes in the page, little to render again.
const SECTION_CHARS = 3000;
const FENCE = /^\s*(```|~~~)/;
const DETAILS_OPEN = /<details[\s>]/gi;
const DETAILS_CLOSE = /<\/details>/gi;

/** The text cut into the raw pieces of its sections. A cut falls on a blank line outside a code block and outside an
 * open <details>, so each piece is a Markdown text of its own. The last piece is the one still being written. */
export function splitSections(text: string): string[] {
  const sections: string[] = [];
  let sectionStart = 0;
  let inFence = false;
  let detailsOpen = 0;
  let lineStart = 0;
  while (lineStart < text.length) {
    const newline = text.indexOf("\n", lineStart);
    const lineEnd = newline === -1 ? text.length : newline;
    const line = text.slice(lineStart, lineEnd);
    const next = newline === -1 ? text.length : newline + 1;
    if (FENCE.test(line)) inFence = !inFence;
    else if (!inFence) detailsOpen += (line.match(DETAILS_OPEN)?.length ?? 0) - (line.match(DETAILS_CLOSE)?.length ?? 0);
    if (line.trim() === "" && !inFence && detailsOpen <= 0 && next - sectionStart >= SECTION_CHARS && next < text.length) {
      sections.push(text.slice(sectionStart, next));
      sectionStart = next;
    }
    lineStart = next;
  }
  sections.push(text.slice(sectionStart));
  return sections;
}

/** The sections of `text` with their HTML. The finished sections of `previous` (all but its last) that are still the
 * start of the text are kept as they are, the rest is cut and rendered anew: with every piece the work is that of the
 * open section, not of the whole text. */
export function renderStream(text: string, previous: readonly StreamSection[]): StreamSection[] {
  const kept: StreamSection[] = [];
  let offset = 0;
  for (const section of previous.slice(0, -1)) {
    if (!text.startsWith(section.raw, offset)) break;
    kept.push(section);
    offset += section.raw.length;
  }
  const rest = text.slice(offset);
  if (rest === "" && kept.length) return kept;
  return [...kept, ...splitSections(rest).map((raw) => ({ raw, html: renderMarkdown(raw) }))];
}
