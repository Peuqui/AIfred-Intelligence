"""Chat bubble artifacts: what a turn's tools produce for the bubble.

The model's text is one part of a bubble. The other parts come from tools:
sub-agent transcripts, fetched web pages, sandbox pages and plots, camera
images and the VLM's description of them. The LLM pipeline collects them as
``BubbleArtifact`` objects, each at the point of the turn where its tool ran,
and ``render_bubble`` is the one place that turns text plus artifacts into
bubble markup, in turn order.

A sub-agent hands its artifacts to the caller as they are (see
``to_dict``/``from_dict``), so a page the sub-agent built or a photo it took
shows up in the caller's bubble exactly like one of the caller's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Kinds and their ``data``
KIND_COLLAPSIBLE = "collapsible"      # {"title": str, "content": str, "icon", "footer": str (optional)}
KIND_SOURCES = "sources"              # {"urls": [{"url": str, "success": bool | None}]}
KIND_SANDBOX_HTML = "sandbox_html"    # {"url": str}
KIND_SANDBOX_IMAGE = "sandbox_image"  # {"url": str}
KIND_VISION_IMAGES = "vision_images"  # {"urls": [str], "alt": str}
KIND_VLM_OUTPUT = "vlm_output"        # {"body": str}


@dataclass
class BubbleArtifact:
    kind: str
    data: dict[str, Any]
    offset: int = 0  # position in the turn's text where the tool ran

    def key(self) -> str | None:
        """Identity of an artifact that must appear only once per turn: for
        sandbox output the content hash in its file name (the same page saved
        under two names is one page), otherwise the URL."""
        if self.kind in (KIND_SANDBOX_HTML, KIND_SANDBOX_IMAGE):
            from .sandbox import sandbox_output_hash
            url = self.data["url"]
            return f"{self.kind}:{sandbox_output_hash(url) or url}"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "data": self.data}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BubbleArtifact:
        return cls(kind=str(raw["kind"]), data=dict(raw["data"]))


def vision_image_urls(artifacts: list[BubbleArtifact]) -> list[str]:
    """All camera image URLs the artifacts show, in turn order."""
    return [
        url
        for artifact in artifacts
        if artifact.kind == KIND_VISION_IMAGES
        for url in artifact.data["urls"]
    ]


def image_markdown(alt: str, url: str) -> str:
    return f"![{alt}]({url})"


def strip_image_markdown(text: str, urls: list[str]) -> str:
    """Remove every ``![…](url)`` of the given URLs from ``text``."""
    for url in urls:
        text = re.sub(r"!\[[^\]]*\]\(" + re.escape(url) + r"\)", "", text)
    return text


def _source_block(url: str) -> str:
    """The page's source code, collapsed below the embedded page, so the
    program can be read without the model copying it into its answer.
    Sandbox output and documents pages alike; "" when the file is gone."""
    from .formatting import build_tool_collapsible, get_ui_locale
    from .i18n import t
    from .sandbox import documents_html_path, sandbox_output_path

    path = sandbox_output_path(url) or documents_html_path(url)
    if path is None:
        return ""
    label = t("collapsible_source_code", lang=get_ui_locale())
    return build_tool_collapsible({
        "title": f"📄 {label} — {path.name}",
        "content": path.read_text(encoding="utf-8"),
    })


def render_artifact(artifact: BubbleArtifact, model_name: str | None, show_tags: bool) -> str:
    """Bubble markup of one artifact ("" when this view does not show it)."""
    from .formatting import (
        build_sandbox_iframe,
        build_sandbox_image,
        build_sources_collapsible,
        build_tool_collapsible,
        format_thinking_process,
    )

    kind, data = artifact.kind, artifact.data
    if kind == KIND_COLLAPSIBLE:
        return build_tool_collapsible(data)
    if kind == KIND_SOURCES:
        urls = data["urls"]
        successful = [
            {"url": u["url"], "word_count": 0, "rank_index": i, "success": True}
            for i, u in enumerate(urls) if u.get("success")
        ]
        failed = [
            {"url": u["url"], "error": "fetch failed", "rank_index": i}
            for i, u in enumerate(urls) if not u.get("success")
        ]
        return build_sources_collapsible(successful, failed)
    if kind == KIND_SANDBOX_HTML:
        return "\n\n".join(filter(None, [build_sandbox_iframe(data["url"]), _source_block(data["url"])]))
    if kind == KIND_SANDBOX_IMAGE:
        return build_sandbox_image(data["url"])
    if kind == KIND_VISION_IMAGES:
        return " ".join(image_markdown(data["alt"], url) for url in data["urls"])
    if kind == KIND_VLM_OUTPUT:
        # The VLM's own text is a tag collapsible like <think>: only in views
        # that show tags.
        if not show_tags:
            return ""
        return format_thinking_process(f"<vlm_output>{data['body']}</vlm_output>", model_name=model_name)
    raise ValueError(f"unknown bubble artifact kind: {kind}")


def render_bubble(
    text: str,
    artifacts: list[BubbleArtifact],
    model_name: str | None = None,
    show_tags: bool = True,
) -> str:
    """Bubble markup for a turn: the text in segments between the artifacts'
    offsets, each artifact where its tool ran.

    ``show_tags`` renders thinking and other tag blocks as collapsibles (chat
    view); without it they are stripped (Message Hub view). Camera images are
    shown by their artifacts; the text keeps its own image references for the
    conversation history, so they are hidden here.
    """
    from .context_manager import strip_thinking_blocks
    from .formatting import format_thinking_process

    hidden_urls = vision_image_urls(artifacts)

    def segment(part: str) -> str:
        part = strip_image_markdown(part, hidden_urls)
        if show_tags:
            return format_thinking_process(part, model_name=model_name)
        return strip_thinking_blocks(part)

    parts: list[str] = []
    position = 0
    for artifact in sorted(artifacts, key=lambda a: a.offset):
        parts.append(segment(text[position:artifact.offset]))
        parts.append(render_artifact(artifact, model_name, show_tags))
        position = artifact.offset
    parts.append(segment(text[position:]))
    return "\n\n".join(part.strip() for part in parts if part.strip())
