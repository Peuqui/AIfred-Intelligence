import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("removes what could run script, from a model's answer", () => {
    const html = renderMarkdown('Bild: <img src="x" onerror="alert(1)"> und <script>alert(2)</script>');
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("<script");
    expect(html).toContain("<img");
  });

  it("keeps the server's own HTML parts: thinking block and mode marker", () => {
    const html = renderMarkdown(
      "<details><summary>Denkprozess</summary>\n\nüberlegt\n\n</details>\n\n" +
        "<span style='color: rgba(255, 255, 255, 0.6); font-style: italic;'>[Tribunal R1]</span>\n\nText",
    );
    expect(html).toContain("<details>");
    expect(html).toContain("<summary>Denkprozess</summary>");
    expect(html).toContain('style="color: rgba(255, 255, 255, 0.6); font-style: italic;"');
  });

  it("opens links in a new tab without access to this page", () => {
    const html = renderMarkdown("[AIfred](https://example.org)");
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("renders code with highlighting and math with KaTeX", () => {
    const html = renderMarkdown("```python\nprint(1)\n```\n\n$a^2$");
    expect(html).toContain("hljs");
    expect(html).toContain("katex");
  });
});
