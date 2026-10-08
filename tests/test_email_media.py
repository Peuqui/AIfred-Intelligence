"""Mail media: Markdown images are embedded (cid), Markdown links to uploads
are attached, and send_email builds the matching MIME tree.

Kontrakt (email_channel/media.py, client.send_email):
- ``![alt](/_upload/…)`` → ``cid:`` im HTML + inline-Teil, ``[text](/_upload/…)``
  → Anhang, Linktext bleibt im Text.
- Nur Dateien der eigenen Session (resolve_outbound_attachment); eine
  nicht auflösbare ``/_upload/``-Referenz wirft, statt still zu fehlen.
- Struktur: mixed(related(alternative(text, html), bild…), anhang…).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aifred.plugins.channels.email_channel import client
from aifred.plugins.channels.email_channel.media import render_mail

SESSION = "sess1"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture
def sandbox_dir(tmp_path):
    session_dir = tmp_path / SESSION
    session_dir.mkdir()
    (session_dir / "graph.png").write_bytes(PNG)
    (session_dir / "data.csv").write_text("a;b\n1;2\n")
    with patch("aifred.lib.config.SANDBOX_OUTPUT_DIR", tmp_path):
        yield session_dir


def _url(name: str, session: str = SESSION) -> str:
    return f"/_upload/sandbox_output/{session}/{name}"


class TestRenderMail:
    def test_image_is_embedded_by_cid(self, sandbox_dir):
        mail = render_mail(f"Preise\n\n![Verlauf]({_url('graph.png')})", SESSION, "email")
        assert [path.name for _, path in mail.inline_images] == ["graph.png"]
        content_id = mail.inline_images[0][0]
        assert f'src="cid:{content_id}"' in mail.html
        assert "_upload" not in mail.html
        assert mail.attachments == []

    def test_plain_text_names_the_file_not_the_server_path(self, sandbox_dir):
        mail = render_mail(f"![Verlauf]({_url('graph.png')})", SESSION, "email")
        assert "[image: Verlauf] (graph.png)" in mail.plain
        assert "_upload" not in mail.plain

    def test_link_becomes_attachment_and_keeps_its_text(self, sandbox_dir):
        mail = render_mail(f"Hier die [Rohdaten]({_url('data.csv')}).", SESSION, "email")
        assert [path.name for path in mail.attachments] == ["data.csv"]
        assert "Rohdaten" in mail.html and "_upload" not in mail.html
        assert "Rohdaten" in mail.plain and "_upload" not in mail.plain
        assert mail.inline_images == []

    def test_inline_and_attachment_together_and_same_file_once(self, sandbox_dir):
        text = (
            f"![A]({_url('graph.png')}) ![B]({_url('graph.png')}) "
            f"[Bild]({_url('graph.png')}) [Daten]({_url('data.csv')})"
        )
        mail = render_mail(text, SESSION, "email")
        assert len(mail.inline_images) == 1
        assert [path.name for path in mail.attachments] == ["graph.png", "data.csv"]

    def test_explicit_attachment_is_kept(self, sandbox_dir):
        mail = render_mail("kein Verweis", SESSION, "email", [sandbox_dir / "data.csv"])
        assert [path.name for path in mail.attachments] == ["data.csv"]

    def test_other_session_raises(self, sandbox_dir):
        with pytest.raises(ValueError):
            render_mail(f"![x]({_url('graph.png', 'fremd')})", SESSION, "email")

    def test_missing_file_raises(self, sandbox_dir):
        with pytest.raises(ValueError):
            render_mail(f"![x]({_url('weg.png')})", SESSION, "email")

    def test_embedding_a_non_image_raises(self, sandbox_dir):
        with pytest.raises(ValueError):
            render_mail(f"![x]({_url('data.csv')})", SESSION, "email")

    def test_ordinary_markdown_is_untouched(self, sandbox_dir):
        mail = render_mail("**fett** und [Link](https://example.org)", SESSION, "email")
        assert "<strong>fett</strong>" in mail.html
        assert 'href="https://example.org"' in mail.html
        assert mail.inline_images == [] and mail.attachments == []


class TestSendEmailMime:
    def _send(self, **kwargs):
        """Run send_email with SMTP/IMAP mocked; return the message it sent."""
        smtp = MagicMock()
        smtp_class = MagicMock()
        smtp_class.return_value.__enter__.return_value = smtp
        values = {"user": "u@example.org", "from": "", "smtp_host": "h", "smtp_port": "587",
                  "password": "p", "sent_folder": ""}
        with patch.object(client.smtplib, "SMTP", smtp_class), \
             patch.object(client.broker, "get", side_effect=lambda _plugin, key: values[key]), \
             patch.object(client, "_imap_connect", side_effect=OSError("kein IMAP im Test")):
            client.send_email(to="a@example.org", subject="s", **kwargs)
        return smtp.send_message.call_args.args[0]

    def test_inline_and_attachment_tree(self, sandbox_dir):
        message = self._send(
            body="text", html='<img src="cid:abc">',
            inline_images=[("abc", str(sandbox_dir / "graph.png"))],
            attachments=[str(sandbox_dir / "data.csv")],
        )
        assert message.get_content_type() == "multipart/mixed"
        body, attachment = message.get_payload()
        assert body.get_content_type() == "multipart/related"
        alternative, image = body.get_payload()
        assert alternative.get_content_type() == "multipart/alternative"
        assert image.get_content_type() == "image/png"
        assert image["Content-ID"] == "<abc>"
        assert image.get_content_disposition() == "inline"
        assert attachment.get_content_disposition() == "attachment"
        assert attachment.get_filename() == "data.csv"

    def test_only_inline_has_no_mixed_wrapper(self, sandbox_dir):
        message = self._send(
            body="text", html='<img src="cid:abc">',
            inline_images=[("abc", str(sandbox_dir / "graph.png"))],
        )
        assert message.get_content_type() == "multipart/related"

    def test_inline_without_html_raises(self, sandbox_dir):
        with pytest.raises(ValueError):
            self._send(body="text", inline_images=[("abc", str(sandbox_dir / "graph.png"))])
