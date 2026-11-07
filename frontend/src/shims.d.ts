// markdown-it-texmath ships no types: a markdown-it plugin taking KaTeX and its delimiters.
declare module "markdown-it-texmath" {
  import type MarkdownIt from "markdown-it";
  const texmath: (md: MarkdownIt, options: { engine: unknown; delimiters: string | string[] }) => void;
  export default texmath;
}
