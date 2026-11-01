"""resolve_outbound_attachment — cross-channel SSOT for tool file attachments.

Security invariants:
- session-scoped sources (uploads, sandbox output) resolve only for the
  OWNING session (VI7 cross-session rejection),
- the shared documents/ folder depends on who asks, not on the channel: the browser,
  the verified owner on any channel and the owner's own scheduled jobs, never a
  foreign sender,
- vigilantia frames are system-wide,
- size cap + existence are enforced.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

import aifred.lib.config as cfg
import aifred.lib.vision_utils as vu
from aifred.lib import security


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

    @pytest.mark.parametrize("source", ["scheduler", "telegram", "email", "ein_neuer_kanal"])
    def test_documents_for_the_verified_owner_on_any_channel(self, dirs, source):
        _write(dirs["doc"], "preise.png")
        path, err = vu.resolve_outbound_attachment("/_upload/documents/preise.png", SID, source, "owner")
        assert err is None and path is not None

    @pytest.mark.parametrize("source", ["email", "telegram", "discord", "ai_connect", "freeecho2", "ein_neuer_kanal", ""])
    def test_documents_refused_to_anyone_else(self, dirs, source):
        _write(dirs["doc"], "shared.pdf")
        path, err = vu.resolve_outbound_attachment("/_upload/documents/shared.pdf", SID, source, "external")
        assert path is None and err is not None

    def test_documents_refused_without_a_verdict(self, dirs):
        _write(dirs["doc"], "shared.pdf")
        path, err = vu.resolve_outbound_attachment("/_upload/documents/shared.pdf", SID, "telegram")
        assert path is None and err is not None

    def test_webhook_never_reaches_documents(self, dirs):
        _write(dirs["doc"], "shared.pdf")
        path, err = vu.resolve_outbound_attachment("/_upload/documents/shared.pdf", SID, "webhook", "owner")
        assert path is None and err is not None

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


class TestScheduledMail:
    """A job's report links its chart from documents/: the mail goes out with it, while a
    reply to an outside sender with the same link stays refused."""

    REPORT = "Der Bericht.\n\n![Verlauf](/_upload/documents/heizoel/preise.png)"

    def _send(self, monkeypatch, metadata: dict, sender: str = "system", inbound: dict | None = None) -> list[dict]:
        from aifred.lib.envelope import InboundMessage, OutboundMessage
        from aifred.plugins.channels import email_channel
        from aifred.plugins.channels.email_channel import client

        sent: list[dict] = []
        monkeypatch.setattr(client, "send_email", lambda **kwargs: sent.append(kwargs))
        outbound = OutboundMessage(channel="email", channel_id="x", recipient="owner@example.test", text=self.REPORT,
                                   metadata={"session_id": SID, "subject": "Bericht", **metadata})
        original = InboundMessage(channel="email", channel_id="x", sender=sender, text="", timestamp=datetime.now(),
                                  metadata=inbound or {})
        asyncio.run(email_channel.EmailChannel().send_reply(outbound, original))
        return sent

    def test_a_job_report_goes_out_with_its_chart(self, dirs, monkeypatch):
        _write(dirs["doc"] / "heizoel", "preise.png")
        sent = self._send(monkeypatch, {"source": "scheduler"})
        assert len(sent) == 1 and len(sent[0]["inline_images"]) == 1

    def test_a_reply_to_a_stranger_cannot_attach_from_documents(self, dirs, monkeypatch):
        _write(dirs["doc"] / "heizoel", "preise.png")
        with pytest.raises(ValueError, match="Could not resolve attachment"):
            self._send(monkeypatch, {}, sender="fremd@example.test", inbound={"auth_results": "pass"})

    def test_a_reply_to_the_verified_owner_can(self, dirs, monkeypatch):
        _write(dirs["doc"] / "heizoel", "preise.png")
        monkeypatch.setattr(security, "first_allowlist_entry", lambda *_args: "owner@example.test")
        sent = self._send(monkeypatch, {}, sender="owner@example.test", inbound={"auth_results": "pass"})
        assert len(sent[0]["inline_images"]) == 1

    def test_a_forged_owner_address_does_not_help(self, dirs, monkeypatch):
        _write(dirs["doc"] / "heizoel", "preise.png")
        monkeypatch.setattr(security, "first_allowlist_entry", lambda *_args: "owner@example.test")
        with pytest.raises(ValueError, match="Could not resolve attachment"):
            self._send(monkeypatch, {}, sender="owner@example.test", inbound={"auth_results": "fail"})

    def test_the_scheduler_says_who_it_is(self, monkeypatch):
        from aifred.lib import message_processor, scheduler

        calls: list[dict] = []

        async def announce(_channel, _recipient, _text, *, session_id, metadata):
            calls.append(metadata)
            return True

        monkeypatch.setattr(message_processor, "announce_to_channel", announce)
        job = SimpleNamespace(name="Heizöl", payload={"channel": "email", "recipient": "Lord Helmchen"})
        asyncio.run(scheduler._deliver_announce(job, "Text", SID))
        assert calls == [{"subject": "Heizöl", "source": "scheduler"}]
