// Chat text to safe HTML. The server's bubbles hold Markdown with HTML parts of its own making
// (thinking as <details>, mode markers as <span style>), so HTML stays on; DOMPurify then removes
// whatever could run script (an answer may contain <img onerror=…>), before v-html sees it.
import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
import MarkdownIt, { type StateCore, type Token } from "markdown-it";
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

// A film of the camera (burst film, recording: /_upload/vigilantia/films|recordings/….mp4) that the text names, as a
// link or as a bare path, plays right there instead of being a link to open (the page's markup only: a chat channel
// gets the text with its link). The link's words stay as a line above the player.
const FILM_PATH = /\/_upload\/vigilantia\/(?:films|recordings)\/[^\s)"'<>]+\.mp4/g;
const FILM_PATH_ONLY = new RegExp(`^${FILM_PATH.source}$`);

function inlineToken(state: StateCore, type: "html_inline" | "text", content: string): Token {
  const token = new state.Token(type, "", 0);
  token.content = content;
  return token;
}

function filmTokens(state: StateCore, url: string, label: string): Token[] {
  const player =
    `<video controls preload="metadata" playsinline style="max-width:100%;border-radius:8px" ` +
    `src="${markdown.utils.escapeHtml(url)}"></video>`;
  const caption = label ? `<span style="display:block">🎬 ${markdown.utils.escapeHtml(label)}</span>` : "";
  return [inlineToken(state, "html_inline", caption + player)];
}

markdown.core.ruler.push("films", (state) => {
  for (const block of state.tokens) {
    if (block.type !== "inline" || !block.children) continue;
    const children: Token[] = [];
    for (let at = 0; at < block.children.length; at++) {
      const token = block.children[at] as Token;
      const href = token.type === "link_open" ? String(token.attrGet("href") ?? "") : "";
      if (FILM_PATH_ONLY.test(new URL(href || "x:", window.location.origin).pathname)) {
        let close = at + 1;
        while (close < block.children.length && block.children[close]?.type !== "link_close") close++;
        const label = block.children.slice(at + 1, close).map((word) => word.content).join("");
        children.push(...filmTokens(state, new URL(href, window.location.origin).pathname, label));
        at = close;
      } else if (token.type === "text" && FILM_PATH.test(token.content)) {
        FILM_PATH.lastIndex = 0;
        let rest = 0;
        for (const found of token.content.matchAll(FILM_PATH)) {
          if (found.index > rest) children.push(inlineToken(state, "text", token.content.slice(rest, found.index)));
          children.push(...filmTokens(state, found[0], ""));
          rest = found.index + found[0].length;
        }
        if (rest < token.content.length) children.push(inlineToken(state, "text", token.content.slice(rest)));
      } else {
        children.push(token);
      }
      FILM_PATH.lastIndex = 0;
    }
    block.children = children;
  }
});

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

// The metrics line under an answer (formatting.py writes it as *( TTFT: … )* or *( Inference: … )*), marked so the bubble
// sets it in the small size; chat_export.py does the same for the export.
// After sanitizing, the line's no-break spaces stand as &nbsp;.
const METRICS_LINE = /<em>(\((?:\s|&nbsp;)*(?:TTFT|Inference):)/g;

export function renderMarkdown(text: string): string {
  const html = DOMPurify.sanitize(markdown.render(text), {
    ADD_TAGS: ["iframe"],
    ADD_ATTR: ["target", "sandbox", "scrolling", "loading"],
  });
  return html.replace(METRICS_LINE, '<em class="metrics">$1');
}
