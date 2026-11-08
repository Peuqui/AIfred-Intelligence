// markdown-it-texmath ships no types: a markdown-it plugin taking KaTeX and its delimiters.
declare module "markdown-it-texmath" {
  import type MarkdownIt from "markdown-it";
  const texmath: (md: MarkdownIt, options: { engine: unknown; delimiters: string | string[] }) => void;
  export default texmath;
}

// VueFinder's translations ship without types: one object of English text → translation.
declare module "vuefinder/dist/locales/*" {
  const locale: Record<string, unknown>;
  export default locale;
}

// The id of this build (vite.config.ts, define).
declare const __BUILD_ID__: string;
