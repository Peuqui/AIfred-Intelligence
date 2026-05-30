# Judaica Plugin

**File:** `aifred/plugins/tools/judaica/`

Access to the Jewish source corpus — Tanakh, Talmud, Mishnah, Midrash, Halacha
and the classic Torah commentaries (Rashi, Ramban, Ibn Ezra). One tool bundles
two access paths: an exact passage lookup and a thematic vector search.

The plugin is only available when the source folder
`data/documents/judaica/` exists (`is_available()`).

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `search_judaica` | Search the Judaica corpus. The mode is chosen automatically from the query (see below). | READONLY |

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | string | yes | A reference (e.g. `Berakhot 3`, `Pirkei Avot 1,1`) or a topic (e.g. `What does the Talmud say about repentance?`) |

## Two modes (chosen automatically)

`search_judaica` inspects the query and picks one of two paths:

1. **Reference lookup** — if the query names a passage (`Berakhot 3`,
   `Pirkei Avot 1,1`, `Rashi zu Genesis 1,1`), the exact source text is returned
   (Hebrew original + translation) from the structured Judaica JSON.
   - One number = the whole section (`Berakhot 3` → all of Daf 3).
   - Two numbers = section + entry (`Pirkei Avot 1,1`), optionally a range
     (`Pirkei Avot 1,1-3`).
   - Talmud Bavli citations may use the Vilna amud suffix (`Sanhedrin 97b`); it
     is translated to the Sefaria-style continuous daf internally.
2. **Thematic search** — any other query runs a vector search restricted to the
   `judaica` folder (recursive prefix match, so sub-folders like
   `judaica/talmud`, `judaica/tanakh/tora` are included). It reuses the shared
   `file_manager.search_index` (`n_results=8`), so there is no duplicated vector
   logic.

If the structured index is missing, reference lookups fall through to the
thematic search.

## Setup

The texts under `data/documents/` are not version-controlled, so the data must
be (re)created after cloning:

```bash
python scripts/download_judaica.py      # fetch the Sefaria source texts
python scripts/build_judaica_json.py    # build the *.json + _index.json lookup data
```

`build_judaica_json.py` writes one structured JSON per work plus an
`_index.json` listing every work with the names the reference lookup recognises
it by (`section_type`/`entry_type` differ per work — Daf/Line for the Talmud,
Chapter/Verse for the Tanakh, …).

## See also

- `search_bible` for the Christian Bible
- `search_documents` for other documents
