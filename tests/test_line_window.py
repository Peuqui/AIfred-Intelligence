"""A range of lines from a file of any size: what stays in memory, and what the range says."""
import pytest

from aifred.lib.line_window import LineTooLong, LineWindow, line_range

TEXT = "a\nb\nc"


@pytest.mark.parametrize(("start", "end", "text", "words"), [
    (0, 0, TEXT, "all 3 lines"),
    (2, 3, "b\nc", "lines 2-3 of 3"),
    (2, 0, "b\nc", "lines 2-3 of 3"),
    (0, 2, "a\nb", "lines 1-2 of 3"),
    (3, 99, "c", "lines 3-3 of 3"),
    (5, 9, "", "lines 5-3 of 3"),
])
def test_a_line_range(start: int, end: int, text: str, words: str) -> None:
    assert line_range(TEXT, start, end) == (text, 3, words)


@pytest.mark.parametrize("text", ["", "a", "a\n", "\n\n", "a\n\nb\n"])
def test_the_whole_text_comes_back_unchanged(text: str) -> None:
    assert line_range(text, 0, 0) == (text, len(text.split("\n")), f"all {len(text.split(chr(10)))} lines")


def _fed(data: bytes, size: int, *args: int) -> tuple[str, int, str]:
    window = LineWindow(*args)
    for start in range(0, len(data), size):
        window.feed(data[start:start + size])
    return window.result()


@pytest.mark.parametrize("size", [1, 2, 3, 7, 1000])
def test_chunks_may_split_lines_and_letters_anywhere(size: int) -> None:
    data = "Grüße\nHeizöl für 100 €\nEnde".encode()
    assert _fed(data, size, 2, 2) == ("Heizöl für 100 €", 3, "lines 2-2 of 3")
    assert _fed(data, size) == (data.decode(), 3, "all 3 lines")


def test_a_huge_file_keeps_only_the_range_and_still_counts_every_line() -> None:
    window = LineWindow(1_999_999, 2_000_000)
    for start in range(0, 2_000_000, 1000):
        window.feed("".join(f"Zeile {number}\n" for number in range(start + 1, start + 1001)).encode())
    assert len(window._kept) == 2
    assert window.result() == ("Zeile 1999999\nZeile 2000000", 2_000_001, "lines 1999999-2000000 of 2000001")


def test_a_range_that_outgrows_the_memory_names_the_lines_it_holds() -> None:
    window = LineWindow(max_chars=25)
    window.feed(b"zehn Bytes\nzehn Bytes\nzehn Bytes\nzehn Bytes\n")
    assert window.result() == ("zehn Bytes\nzehn Bytes", 5, "lines 1-2 of 5")


def test_a_binary_file_fails_at_its_first_invalid_byte() -> None:
    window = LineWindow()
    window.feed(b"PNG")
    with pytest.raises(UnicodeDecodeError):
        window.feed(b"\x89\xff\xfe")


def test_a_text_cut_inside_a_letter_is_no_text() -> None:
    window = LineWindow()
    window.feed("ü".encode()[:1])
    with pytest.raises(UnicodeDecodeError):
        window.result()


def test_an_endless_line_cannot_fill_the_memory() -> None:
    window = LineWindow(max_chars=10)
    with pytest.raises(LineTooLong, match="line 2"):
        window.feed(b"kurz\n" + b"x" * 11)


def test_the_charset_of_the_answer_counts() -> None:
    window = LineWindow(encoding="latin-1")
    window.feed("Grüße".encode("latin-1"))
    assert window.result()[0] == "Grüße"
