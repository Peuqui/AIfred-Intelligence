# Bible Plugin

**File:** `aifred/plugins/tools/bible/`

Read access to the Bible via a single tool. It combines two lookup paths:
an exact passage lookup for a named reference, and a thematic vector search
for any other query. The mode is chosen automatically from the query.

The plugin is only available when at least one Bible translation is present
under `data/documents/bibel/`.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `search_bible` | Bible lookup. Two modes, chosen automatically from the query: a named passage (e.g. `Psalm 5`, `Joh 3,16`, `1. Mose 1,1-5`) returns the exact verse text; any other (topical) query runs a thematic search and returns related verses. | READONLY |

### Parameter

- `query` (string, required) — a scripture reference (e.g. `Psalm 5`,
  `Joh 3,16`) or a topic (e.g. `verses about comfort`).

## Modes

1. **Exact lookup** — when the query parses as a named reference
   (`<book> <chapter>[,<verse>[-<verse>]]`), the exact verse text is read
   from the active translation's structured JSON. The result contains the
   reference, book, chapter, translation name and the list of verses. A
   reference without a verse returns the whole chapter; a verse range
   returns each verse in the range.
2. **Thematic search** — any other query runs a vector search restricted to
   the `bibel` folder (via the shared document store). The result contains
   the query and a list of matching verse snippets with their filenames.

Book recognition is data-driven: canonical book names come from the loaded
Bible JSON, abbreviations from a per-language alias table
(`book_aliases/<lang>.json`, shipped for `de` and `en`).

## Configuration

- **Translation** — the plugin setting `BIBLE_TRANSLATION` selects which
  sub-folder of `data/documents/bibel/` the exact lookup reads. It is shown
  as a dropdown in the plugin settings; the options are the available
  translation folders. When unset, the first available translation is used.
- **Data** — each translation is a sub-folder of `data/documents/bibel/`
  holding a structured book/chapter/verse JSON. Such a JSON can be built
  with `scripts/build_bible.py`. Any 66-book Bible in any language works by
  dropping its JSON folder in; a new language only needs its own
  `book_aliases/<lang>.json`.
