"""Finding words in text, shared by the document search and the mail index.

Both keep an SQLite FTS5 index on trigrams: unlike an index of whole words it finds
parts of words too ("Heizöl" in "Heizölrechnung"), which German compounds need.
"""

import re
import string
import unicodedata

# The trigram tokenizer has no token for anything shorter.
MIN_INDEXED_WORD_LENGTH = 3
_WORD_EDGES = string.punctuation + "„“”‚‘’«»…–—"
# Combining marks of Latin text (umlauts after NFD, transliterations like "A͑qiba") and
# the Hebrew points and accents, not its maqaf and sof pasuq, which separate words.
_MARKS = re.compile("[̀-֑ͯ-ׇֽֿׁׂׅׄ]")


def without_marks(text: str) -> str:
    """``text`` as a word index holds it. The tokenizer's own remove_diacritics handles
    precomposed letters only, a separate combining mark stays in the word. Indexed text
    and query words both pass here, so "Müller" still meets "Müller"."""
    return _MARKS.sub("", unicodedata.normalize("NFD", text))


def query_words(query: str) -> list[str]:
    """The distinct words of ``query``, in the form the index holds them."""
    words = (raw.strip(_WORD_EDGES) for raw in without_marks(query).split())
    return list(dict.fromkeys(word for word in words if word))


def fts_phrase(word: str) -> str:
    """``word`` for an FTS5 expression, quoted so that nothing in it counts as syntax."""
    return '"' + word.replace('"', '""') + '"'
