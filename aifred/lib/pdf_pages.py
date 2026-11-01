"""PDF pages as images, for the pages that have no text: a scan is a picture of a page.
They go to the session's upload folder, where the vision tools can reach them."""

from typing import Any

from .config import READ_FILE_RENDER_DPI, READ_FILE_RENDER_MAX_PAGES
from .vision_utils import get_image_url, save_session_upload


def page_selection(pages: str, total_pages: int) -> list[int]:
    """0-based page indices for a range like "3", "1-5", "3,7,10-12"."""
    selected: list[int] = []
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            # Clamp to the actual page count BEFORE building the range —
            # otherwise "1-2000000000" materializes a two-billion-element
            # list and OOMs the worker.
            selected.extend(range(max(1, int(start)) - 1, min(int(end), total_pages)))
        else:
            selected.append(int(part) - 1)
    return [p for p in selected if 0 <= p < total_pages]


def render_scanned_pages(document: Any, selected: list[int], stem: str, session_id: str) -> tuple[list[tuple[int, str]], list[int]]:
    """Render the selected pages without text, READ_FILE_RENDER_MAX_PAGES at a time: one
    message to the vision model holds no more. Returns (page number, image URL) of those
    done and the page numbers left for the next call."""
    scanned = [index for index in selected if not document[index].get_text().strip()]
    done: list[tuple[int, str]] = []
    for index in scanned[:READ_FILE_RENDER_MAX_PAGES]:
        png = document[index].get_pixmap(dpi=READ_FILE_RENDER_DPI).tobytes("png")
        done.append((index + 1, get_image_url(save_session_upload(png, session_id, f"{stem}_p{index + 1}", "png"))))
    return done, [index + 1 for index in scanned[READ_FILE_RENDER_MAX_PAGES:]]
