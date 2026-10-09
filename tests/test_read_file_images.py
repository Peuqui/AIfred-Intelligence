"""read_file: gescannte PDF-Seiten und Bilddateien werden zu Bild-URLs im Upload-Ordner der Sitzung."""

import pymupdf
import pytest
from PIL import Image

from aifred.lib import config, vision_utils
from aifred.plugins.tools.workspace import _page_selection, _read_image, _read_pdf

SESSION = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    target = tmp_path / "upload" / "images"
    monkeypatch.setattr(vision_utils, "UPLOAD_IMAGES_DIR", target)
    return target


def _scan_png(path):
    Image.new("RGB", (200, 280), "white").save(path)
    return path


@pytest.fixture
def mixed_pdf(tmp_path):
    """Seite 1 mit Textebene, Seiten 2 und 3 nur Bild (wie ein Scan)."""
    scan = _scan_png(tmp_path / "scan.png")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Rechnung Nr. 4711")
    for _ in range(2):
        doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(scan))
    path = tmp_path / "Brief.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_page_selection_ranges_and_clamping():
    assert _page_selection("3,1-2", 5) == [2, 0, 1]
    assert _page_selection("4-2000000000", 5) == [3, 4]
    assert _page_selection("9", 5) == []


def test_text_pages_stay_text_scanned_pages_become_images(upload_dir, mixed_pdf):
    result, urls = _read_pdf(mixed_pdf, "", SESSION)
    assert "Rechnung Nr. 4711" in result["content"]
    assert "--- Page 2 ---" not in result["content"]
    assert [p["page"] for p in result["image_pages"]] == [2, 3]
    assert urls == [p["url"] for p in result["image_pages"]]
    assert all(u.startswith(f"/_upload/images/{SESSION}/Brief_p") for u in urls)
    assert len(list((upload_dir / SESSION).glob("Brief_p*.png"))) == 2


def test_page_selection_limits_rendering(upload_dir, mixed_pdf):
    result, urls = _read_pdf(mixed_pdf, "3", SESSION)
    assert result["content"] == ""
    assert [p["page"] for p in result["image_pages"]] == [3]


def test_render_cap_reports_the_rest(upload_dir, mixed_pdf, monkeypatch):
    monkeypatch.setattr(config, "READ_FILE_RENDER_MAX_PAGES", 1)
    result, urls = _read_pdf(mixed_pdf, "", SESSION)
    assert [p["page"] for p in result["image_pages"]] == [2]
    assert result["image_pages_not_rendered"] == [3]


def test_image_file_is_copied_into_the_session_upload(upload_dir, tmp_path):
    photo = _scan_png(tmp_path / "Zaehlerstand.png")
    result, urls = _read_image(photo, SESSION)
    assert result["type"] == "image"
    assert urls == [result["image_url"]]
    assert result["image_url"].startswith(f"/_upload/images/{SESSION}/Zaehlerstand_")
    assert vision_utils.url_to_file_path(result["image_url"], SESSION).exists()


def test_without_valid_session_nothing_is_stored(upload_dir, mixed_pdf):
    with pytest.raises(ValueError):
        _read_pdf(mixed_pdf, "", "")
