// Chat text to safe HTML. The server's bubbles hold Markdown with HTML parts of its own making
// (thinking as <details>, mode markers as <span style>), so HTML stays on; DOMPurify then removes
// whatever could run script (an answer may contain <img onerror=…>), before v-html sees it.
import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
import MarkdownIt from "markdown-it";
import texmath from "markdown-it-texmath";
import katex from "katex";

const markdown = new MarkdownIt({ html: true, linkify: true, breaks: true }).use(texmath, {
  engine: katex,
  delimiters: ["dollars", "brackets"],
});
markdown.set({
  highlight(code: string, language: string): string {
    if (language && hljs.getLanguage(language)) {
      return hljs.highlight(code, { language, ignoreIllegals: true }).value;
    }
    return markdown.utils.escapeHtml(code);
  },
});

// Links open in a new tab, without access to this page.
const defaultLinkOpen = markdown.renderer.rules.link_open;
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  const token = tokens[index];
  if (token) {
    token.attrSet("target", "_blank");
    token.attrSet("rel", "noopener noreferrer");
  }
  return defaultLinkOpen ? defaultLinkOpen(tokens, index, options, env, self) : self.renderToken(tokens, index, options);
};

// The server's sandbox visualisations (formatting.build_sandbox_iframe): an iframe stays when it shows AIfred's own
// sandbox output (resolved, so "../" leads nowhere else) and always runs sandboxed, scripts only, without access
// to this page. Every other iframe goes.
const SANDBOX_OUTPUT_PATH = "/_upload/sandbox_output/";

function isSandboxOutput(src: string | null): boolean {
  if (!src) return false;
  const url = new URL(src, window.location.origin);
  return url.origin === window.location.origin && url.pathname.startsWith(SANDBOX_OUTPUT_PATH);
}

DOMPurify.addHook("uponSanitizeElement", (node, data) => {
  if (data.tagName === "iframe" && !isSandboxOutput((node as Element).getAttribute("src"))) {
    node.parentNode?.removeChild(node);
  }
});
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "IFRAME") node.setAttribute("sandbox", "allow-scripts");
});

export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(markdown.render(text), {
    ADD_TAGS: ["iframe"],
    ADD_ATTR: ["target", "sandbox", "scrolling", "loading"],
  });
}
