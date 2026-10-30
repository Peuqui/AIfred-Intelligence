"""A range of lines from a file of any size.

The file arrives in chunks and passes through: only the wanted lines stay in
memory, all others are just counted. So no file is too large to read, and an
agent reaches any place of it by line number. A binary file fails at its first
invalid byte (``UnicodeDecodeError``), so the rest is never fetched.
"""

import codecs

from .config import WORKSPACE_READ_MAX_BYTES


class LineTooLong(ValueError):
    """One line alone does not fit into memory, so no range reaches past it."""


class LineWindow:
    """Keeps the lines ``line_start``..``line_end`` (1-based, inclusive, 0 = no
    bound) of what is fed in, at most ``max_chars`` of them."""

    def __init__(self, line_start: int = 0, line_end: int = 0, encoding: str = "utf-8",
                 max_chars: int = WORKSPACE_READ_MAX_BYTES) -> None:
        self._whole = line_start <= 0 and line_end <= 0
        self._first = max(1, line_start)
        self._last = line_end
        self._max_chars = max_chars
        self._decoder = codecs.getincrementaldecoder(encoding)()
        self._partial = ""  # the line still arriving
        self._lines = 0     # lines complete so far
        self._kept: list[str] = []
        self._kept_chars = 0
        self._full = False

    def feed(self, chunk: bytes) -> None:
        self.feed_text(self._decoder.decode(chunk))

    def feed_text(self, text: str) -> None:
        *complete, self._partial = (self._partial + text).split("\n")
        for line in complete:
            self._take(line)
        if len(self._partial) > self._max_chars:
            raise LineTooLong(f"line {self._lines + 1} is longer than {self._max_chars} characters")

    def _take(self, line: str) -> None:
        self._lines += 1
        if self._full or self._lines < self._first or 0 < self._last < self._lines:
            return
        if self._kept_chars + len(line) > self._max_chars:
            self._full = True
            return
        self._kept.append(line)
        self._kept_chars += len(line) + 1

    def result(self) -> tuple[str, int, str]:
        """The kept text, the file's line count and the range in words. A range
        that outgrew the memory names the lines it holds, so the next read goes on there."""
        self._take(self._partial + self._decoder.decode(b"", final=True))
        text = "\n".join(self._kept)
        if self._whole and not self._full:
            return text, self._lines, f"all {self._lines} lines"
        last = min(self._first + len(self._kept) - 1, self._lines)
        return text, self._lines, f"lines {self._first}-{last} of {self._lines}"


def line_range(text: str, line_start: int, line_end: int) -> tuple[str, int, str]:
    """:class:`LineWindow` for a text that is in memory already."""
    window = LineWindow(line_start, line_end)
    window.feed_text(text)
    return window.result()
