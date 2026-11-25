"""CSV and Excel become one Markdown table shape (document_store._markdown_table) for the index and the viewer."""

from pathlib import Path

from aifred.lib.document_store import _markdown_table, _read_csv


def test_a_german_csv_with_semicolons_keeps_its_decimal_commas(tmp_path: Path) -> None:
    file = tmp_path / "preise.csv"
    file.write_text("Datum;Haendler;EUR_je_100L\n2026-10-09;Keck Energieservice;175,91\n", encoding="utf-8")
    assert _read_csv(file) == (
        "| Datum | Haendler | EUR_je_100L |\n| --- | --- | --- |\n| 2026-10-09 | Keck Energieservice | 175,91 |"
    )


def test_a_comma_csv_with_quoted_fields_tabs_and_empty_lines(tmp_path: Path) -> None:
    file = tmp_path / "a.csv"
    file.write_text('Teil,Preis\n"Tank 0,8L",64.98\n\n"zwei\nZeilen",1\n', encoding="utf-8")
    assert _read_csv(file).splitlines()[2:] == ["| Tank 0,8L | 64.98 |", "| zwei Zeilen | 1 |"]
    tabbed = tmp_path / "b.csv"
    tabbed.write_text("a\tb\n1\t2\n", encoding="utf-8")
    assert _read_csv(tabbed).splitlines()[2] == "| 1 | 2 |"


def test_a_single_column_csv_is_read_although_there_is_no_separator(tmp_path: Path) -> None:
    file = tmp_path / "c.csv"
    file.write_text("Name\nAAP01\n", encoding="utf-8")
    assert _read_csv(file) == "| Name |\n| --- |\n| AAP01 |"


def test_a_pipe_in_a_cell_is_escaped_and_short_rows_are_filled() -> None:
    assert _markdown_table([["a", "b", "c"], ["x|y"], ["1", "2", "3"]]).splitlines() == [
        "| a | b | c |", "| --- | --- | --- |", "| x\\|y |  |  |", "| 1 | 2 | 3 |",
    ]
