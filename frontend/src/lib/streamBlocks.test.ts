import { expect, test, vi } from "vitest";

const rendered: string[] = [];
vi.mock("./markdown", () => ({ renderMarkdown: (text: string) => { rendered.push(text); return `<p>${text}</p>`; } }));

const { renderStream, splitSections } = await import("./streamBlocks");

const paragraph = (n: number): string => `Absatz ${n}: ${"x".repeat(400)}\n\n`;
const paragraphs = (count: number): string => Array.from({ length: count }, (_, at) => paragraph(at)).join("");

test("a short text is one section, a long one is cut at blank lines and the pieces together are the text", () => {
  expect(splitSections("kurz\n\ntext")).toEqual(["kurz\n\ntext"]);
  const text = paragraphs(20);
  const pieces = splitSections(text);
  expect(pieces.length).toBeGreaterThan(2);
  expect(pieces.join("")).toBe(text);
  for (const piece of pieces.slice(0, -1)) expect(piece.endsWith("\n\n")).toBe(true);
});

test("a code block and an open <details> are not cut, even with blank lines inside", () => {
  const code = "```js\n" + `${"a".repeat(400)}\n\n`.repeat(12) + "```\n\n";
  const text = paragraph(1) + code + paragraph(2) + paragraph(3);
  expect(splitSections(text).join("")).toBe(text);
  for (const piece of splitSections(text)) expect((piece.match(/```/g) ?? []).length % 2).toBe(0);
  const details = "<details><summary>x</summary>\n\n" + `${"b".repeat(400)}\n\n`.repeat(12) + "</details>\n\n";
  const withDetails = paragraph(1) + details + paragraph(2) + paragraph(3);
  expect(splitSections(withDetails).join("")).toBe(withDetails);
  for (const piece of splitSections(withDetails)) {
    expect((piece.match(/<details/g) ?? []).length).toBe((piece.match(/<\/details>/g) ?? []).length);
  }
});

test("with every piece only the open section is rendered again, the finished ones are the same objects", () => {
  let text = paragraphs(30);
  let sections = renderStream(text, []);
  expect(sections.map((section) => section.raw).join("")).toBe(text);
  const finished = sections.slice(0, -1);
  expect(finished.length).toBeGreaterThan(2);
  rendered.length = 0;
  text += "Noch ein wenig";
  sections = renderStream(text, sections);
  expect(rendered.length).toBe(1);
  finished.forEach((section, at) => expect(sections[at]).toBe(section));
  expect(sections.map((section) => section.raw).join("")).toBe(text);
});

test("a text that no longer begins as before is cut anew", () => {
  const sections = renderStream(paragraphs(30), []);
  const other = "Ganz anderer Text\n\n" + paragraphs(30);
  expect(renderStream(other, sections).map((section) => section.raw).join("")).toBe(other);
});
