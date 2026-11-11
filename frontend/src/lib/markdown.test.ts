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

describe("films of the camera", () => {
  const film = "/_upload/vigilantia/films/cam_rtsp_reolink/2026-11-09/c-1_wide.mp4";

  it("plays a film named as a link, with the link's words above", () => {
    const html = renderMarkdown(`Hier: [Weitwinkel](${film})`);
    expect(html).toContain("<video controls");
    expect(html).toContain(`src="${film}"`);
    expect(html).toContain("🎬 Weitwinkel");
    expect(html).not.toContain("<a ");
  });

  it("plays a film named as a bare path, in the middle of a sentence", () => {
    const html = renderMarkdown(`Der Film ${film} zeigt eine Person.`);
    expect(html).toContain(`src="${film}"`);
    expect(html).toContain("Der Film");
    expect(html).toContain("zeigt eine Person.");
  });

  it("plays a recording as well, and leaves other mp4 links alone", () => {
    expect(renderMarkdown("[x](/_upload/vigilantia/recordings/cam/2026-11-12/a_6s.mp4)")).toContain("<video");
    const other = renderMarkdown("[x](https://example.org/film.mp4) [y](/_upload/images/abc/film.mp4)");
    expect(other).not.toContain("<video");
    expect(other).toContain("<a ");
  });
});
