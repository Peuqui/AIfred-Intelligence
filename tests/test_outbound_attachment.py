"""resolve_outbound_attachment — cross-channel SSOT for tool file attachments.

Security invariants:
- session-scoped sources (uploads, sandbox output) resolve only for the
  OWNING session (VI7 cross-session rejection),
- the shared documents/ folder is browser-only,
- vigilantia frames are system-wide,
- size cap + existence are enforced.
"""

import pytest

import aifred.lib.vision_utils as vu
import aifred.lib.config as cfg


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    up = tmp_path / "upload" / "images"
    sb = tmp_path / "sandbox_output"
    vg = tmp_path / "vigilantia"
    doc = tmp_path / "documents"
    for d in (up, sb, vg, doc):
        d.mkdir(parents=True)
    monkeypatch.setattr(vu, "UPLOAD_IMAGES_DIR", up)
    monkeypatch.setattr(vu, "VIGILANTIA_DIR", vg)
    monkeypatch.setattr(cfg, "SANDBOX_OUTPUT_DIR", sb)
    monkeypatch.setattr(cfg, "DOCUMENTS_DIR", doc)
    return {"up": up, "sb": sb, "vg": vg, "doc": doc}


SID = "a" * 32
OTHER = "b" * 32


def _write(p, name, data=b"x"):
    (p).mkdir(parents=True, exist_ok=True)
    f = p / name
    f.write_bytes(data)
    return f


class TestResolveOutboundAttachment:
    def test_own_session_upload_ok(self, dirs):
        _write(dirs["up"] / SID, "card.jpg")
        path, err = vu.resolve_outbound_attachment(f"/_upload/images/{SID}/card.jpg", SID, "telegram")
        assert err is None and path is not None and path.name == "card.jpg"

    def test_sandbox_output_own_session_ok(self, dirs):
        _write(dirs["sb"] / SID, "report.pdf")
        path, err = vu.resolve_outbound_attachment(f"/_upload/sandbox_output/{SID}/report.pdf", SID, "telegram")
        assert err is None and path is not None and path.name == "report.pdf"

    def test_cross_session_upload_rejected(self, dirs):
        _write(dirs["up"] / OTHER, "secret.jpg")
        path, err = vu.resolve_outbound_attachment(f"/_upload/images/{OTHER}/secret.jpg", SID, "telegram")
        assert path is None and err is not None

    def test_documents_browser_ok(self, dirs):
        _write(dirs["doc"], "shared.pdf")
        path, err = vu.resolve_outbound_attachment("/_upload/documents/shared.pdf", SID, "browser")
        assert err is None and path is not None

    def test_documents_external_rejected(self, dirs):
        _write(dirs["doc"], "shared.pdf")
        path, err = vu.resolve_outbound_attachment("/_upload/documents/shared.pdf", SID, "telegram")
        assert path is None and err is not None

    def test_vigilantia_systemwide_ok(self, dirs):
        _write(dirs["vg"], "frame.jpg")
        path, err = vu.resolve_outbound_attachment("/_upload/vigilantia/frame.jpg", SID, "email")
        assert err is None and path is not None

    def test_path_traversal_blocked(self, dirs):
        path, err = vu.resolve_outbound_attachment(f"/_upload/images/{SID}/../../secret", SID, "browser")
        assert path is None and err is not None

    def test_size_cap(self, dirs, monkeypatch):
        monkeypatch.setattr(cfg, "OUTBOUND_ATTACHMENT_MAX_BYTES", 4)
        _write(dirs["up"] / SID, "big.jpg", b"toolarge")
        path, err = vu.resolve_outbound_attachment(f"/_upload/images/{SID}/big.jpg", SID, "browser")
        assert path is None and "too large" in (err or "")

    def test_missing_file(self, dirs):
        path, err = vu.resolve_outbound_attachment(f"/_upload/images/{SID}/nope.jpg", SID, "browser")
        assert path is None and err is not None

    def test_empty_reference(self, dirs):
        path, err = vu.resolve_outbound_attachment("", SID, "browser")
        assert path is None and err is not None


class TestIsImageFile:
    def test_images(self):
        from pathlib import Path
        assert vu.is_image_file(Path("a.JPG"))
        assert vu.is_image_file(Path("a.png"))

    def test_non_images(self):
        from pathlib import Path
        assert not vu.is_image_file(Path("a.pdf"))
        assert not vu.is_image_file(Path("a.txt"))
