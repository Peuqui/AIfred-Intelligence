"""Tests für die VI7-Session-Isolation in url_to_file_path.

Eine Session darf nur ihre EIGENEN Uploads
(``upload/images/{session_id}/…``) aufloesen; ein Pfad, der eine fremde
Session nennt, wird abgewiesen. Vigilantia-Frames (systemweite
Kamera-Bilder) sind nicht session-gebunden.
"""

from __future__ import annotations

from aifred.lib.vision_utils import url_to_file_path, UPLOAD_IMAGES_DIR, VIGILANTIA_DIR


class TestSessionIsolation:
    def test_own_upload_resolves(self):
        p = url_to_file_path("/_upload/images/sess-A/pic.jpg", "sess-A")
        assert p is not None
        assert p == (UPLOAD_IMAGES_DIR / "sess-A" / "pic.jpg").resolve()

    def test_foreign_upload_rejected(self):
        # Caller sess-A darf sess-B nicht aufloesen.
        assert url_to_file_path("/_upload/images/sess-B/pic.jpg", "sess-A") is None

    def test_full_url_own_session(self):
        p = url_to_file_path("http://mini:3002/_upload/images/sess-A/x.jpg", "sess-A")
        assert p is not None

    def test_full_url_foreign_session_rejected(self):
        assert url_to_file_path("http://mini:3002/_upload/images/sess-B/x.jpg", "sess-A") is None

    def test_missing_leading_slash_still_checked(self):
        # LLM-Tic "_upload/images/…" ohne fuehrenden Slash — Guard greift trotzdem.
        assert url_to_file_path("_upload/images/sess-B/x.jpg", "sess-A") is None
        assert url_to_file_path("_upload/images/sess-A/x.jpg", "sess-A") is not None

    def test_vigilantia_not_session_bound(self):
        # Kamera-Frames sind systemweit — die Session-Bindung gilt hier nicht.
        p = url_to_file_path("/_upload/vigilantia/cam1/frame.jpg", "irgendeine-session")
        assert p is not None
        assert p == (VIGILANTIA_DIR / "cam1" / "frame.jpg").resolve()

    def test_traversal_still_blocked(self):
        # Path-Traversal bleibt dicht (eigene Session, aber ../-Ausbruch).
        assert url_to_file_path("/_upload/images/sess-A/../../etc/passwd", "sess-A") is None

    def test_unknown_marker_returns_none(self):
        assert url_to_file_path("/something/else.jpg", "sess-A") is None
