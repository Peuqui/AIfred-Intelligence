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

  it("embeds the server's sandbox visualisation, always sandboxed to scripts", () => {
    // Until 08.11.2026 DOMPurify removed every iframe: in Vue a sandbox visualisation was only a link.
    const html = renderMarkdown(
      '<details data-sandbox><summary>📊</summary><iframe src="/_upload/sandbox_output/abc/plot-1.html" ' +
        'sandbox="allow-scripts allow-same-origin" scrolling="no" loading="lazy"></iframe></details>',
    );
    expect(html).toContain('<iframe src="/_upload/sandbox_output/abc/plot-1.html"');
    expect(html).toContain('sandbox="allow-scripts"');
    expect(html).not.toContain("allow-same-origin");
  });

  it("removes every other iframe", () => {
    for (const src of [
      "https://example.org/_upload/sandbox_output/a/b.html",
      "/_upload/sandbox_output/../documents/x.html",
      "/_upload/sandbox_output/%2e%2e/documents/x.html",
      "/api/app/",
      "javascript:alert(1)",
    ]) {
      expect(renderMarkdown(`<iframe src="${src}"></iframe>`)).not.toContain("<iframe");
    }
    expect(renderMarkdown("<iframe></iframe>")).not.toContain("<iframe");
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
