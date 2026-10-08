"""Mail body rendering with embedded images and attachments.

One place turns an agent's Markdown into everything ``send_email`` needs, for
every sending path (channel reply / scheduler announce, ``email`` tool):

* ``![alt](/_upload/…)``  → image embedded in the HTML (``cid:``),
* ``[name](/_upload/…)``  → file attached, the link text stays in the body.

Only ``/_upload/…`` references are touched — they resolve through
``resolve_outbound_attachment`` (session-scoped, path-traversal safe, size
capped), so a caller can only send files from its OWN conversation. Every other
image target is blocked earlier by ``sanitize_outbound`` (no ``cid:`` of the
model's own making). An ``/_upload/`` reference that does not resolve raises:
a report must not go out silently without its graph.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from email.utils import make_msgid
from pathlib import Path

from ....lib.markdown_render import md_to_html, md_to_plain
from ....lib.vision_utils import resolve_outbound_attachment

# ![alt](ref) or [text](ref) where ref is a local upload URL (leading slash
# optional, query/anchor not part of the file name).
_LOCAL_REF = re.compile(r"(!?)\[([^\]]*)\]\(\s*(/?_upload/[^)\s]+)\s*\)")


@dataclass
class RenderedMail:
    plain: str
    html: str
    inline_images: list[tuple[str, Path]] = field(default_factory=list)  # (content-id, file)
    attachments: list[Path] = field(default_factory=list)


def render_mail(
    markdown: str, session_id: str, source: str, extra_attachments: list[Path] | None = None,
) -> RenderedMail:
    """Render ``markdown`` to plain + HTML and collect the files it references.

    ``extra_attachments`` are files the caller already resolved (the ``email``
    tool's explicit ``attachment`` argument).
    """
    inline_images: dict[Path, str] = {}  # file → content id
    placeholders: dict[str, str] = {}    # markdown target → content id
    attachments: list[Path] = list(extra_attachments or [])

    def resolve(ref: str) -> Path:
        path, error = resolve_outbound_attachment(ref, session_id, source)
        if path is None:
            raise ValueError(error)
        return path

    def for_html(match: re.Match[str]) -> str:
        is_image, text, ref = match.groups()
        path = resolve(ref)
        if is_image:
            if not (mimetypes.guess_type(path.name)[0] or "").startswith("image/"):
                raise ValueError(f"Cannot embed {path.name!r} as an image — attach it as a link instead.")
            content_id = inline_images.setdefault(path, make_msgid()[1:-1])
            # mistune rewrites cid: targets to "#harmful-link", so the Markdown
            # carries a neutral target and the cid goes into the finished HTML.
            target = f"inline-image-{len(placeholders)}"
            placeholders[target] = content_id
            return f"![{text}]({target})"
        if path not in attachments:
            attachments.append(path)
        return text

    def for_plain(match: re.Match[str]) -> str:
        is_image, text, ref = match.groups()
        name = resolve(ref).name
        return f"![{text}]({name})" if is_image else text

    html_markdown = _LOCAL_REF.sub(for_html, markdown)
    plain_markdown = _LOCAL_REF.sub(for_plain, markdown)
    html = md_to_html(html_markdown)
    for target, content_id in placeholders.items():
        html = html.replace(f'src="{target}"', f'src="cid:{content_id}"')
    return RenderedMail(
        plain=md_to_plain(plain_markdown),
        html=html,
        inline_images=[(cid, path) for path, cid in inline_images.items()],
        attachments=attachments,
    )
