"""Images of a turn: what the user's bubble shows, what the history keeps, and how the model gets
to see them (the browser state and the server turn both build them here).

The image itself never enters the history: the user turn there carries its ``/_upload/`` URL
(``image_marker``), so a later turn can look again with ``vision_analyze``. Who looks at a new
image is ``vision_routing.chat_describer``: the model that answers when it can see (it gets the
image in its request, ``seeing_question`` and ``seeing_content``), else a describer once, whose description joins the
user turn (``describe_images``) for every agent of the turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .agent_tuning import AgentTuning


@dataclass(frozen=True)
class TurnImage:
    """An image uploaded for a message: its file in the session's upload folder and its URL."""

    name: str
    url: str
    path: Path


def session_images(session_id: str, urls: list[str]) -> list[TurnImage]:
    """The uploaded images of the session behind ``urls``, each under its canonical URL.

    Raises:
        ValueError: a URL is not an upload of this session, or its file is gone.
    """
    from .vision_utils import UPLOAD_IMAGES_DIR, get_image_url, url_to_file_path

    images = []
    for url in urls:
        path = url_to_file_path(url, session_id)
        if path is None or not path.is_relative_to(UPLOAD_IMAGES_DIR.resolve()) or not path.is_file():
            raise ValueError(f"not an image of this session: {url}")
        images.append(TurnImage(path.name, get_image_url(path), path))
    return images


def bubble_text(text: str, images: list[TurnImage], lang: str) -> str:
    """The user's bubble: thumbnails above the text; without text the image names."""
    from .formatting import build_thumbnail_row
    from .i18n import t

    if not images:
        return text
    thumbnails = build_thumbnail_row([image.url for image in images])
    if text.strip():
        return f"{thumbnails}\n\n{text}"
    if len(images) == 1:
        return f"{thumbnails}\n\n📷 {images[0].name}"
    names = ", ".join(image.name for image in images)
    return f"{thumbnails}\n\n📷 {t('images_attached', lang=lang, count=len(images), names=names)}"


def bubble_images(images: list[TurnImage]) -> list[dict[str, str]]:
    """The images as the user entry's metadata keeps them (export, replay)."""
    return [{"name": image.name, "url": image.url} for image in images]


def history_text(text: str, images: list[TurnImage]) -> str:
    """The user turn as the history keeps it: the text and the anchor of its images."""
    from .message_builder import image_marker

    if not images:
        return text
    marker = image_marker([image.url for image in images])
    return f"{text}\n\n{marker}" if text.strip() else marker


def seeing_question(images: list[TurnImage], text: str, stamp: str, lang: str) -> str:
    """The question of a model that sees the images itself, stamped, without a question the
    default one. It carries the images' URLs for the current turn: the image itself is only base64
    in the request, and without its URL the model cannot hand the file to a tool."""
    from .message_builder import stamp_user_turn
    from .prompt_loader import load_prompt

    question = text.strip() or load_prompt("vision/default_question", lang=lang)
    reference = load_prompt("vision/image_url_reference", lang=lang, urls=", ".join(image.url for image in images))
    return f"{stamp_user_turn(question, stamp)}\n\n{reference}"


def seeing_content(images: list[TurnImage], question: str, *, no_think: bool) -> list[dict[str, Any]]:
    """The user turn of a model that sees the images itself: the images first (vision models handle
    that order best), then the question as ONE text part. A second text part after the images breaks
    the alignment of the chat template and the vision tower (the model then only thinks and stops
    without tool calls). ``no_think``: Qwen3-VL follows the ``/no_think`` prefix in front (Ollama
    ignores the API's thinking switch for vision models)."""
    from .vision_utils import load_image_as_base64

    content: list[dict[str, Any]] = [{"type": "text", "text": "/no_think"}] if no_think else []
    for image in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{load_image_as_base64(image.path)}"},
        })
    content.append({"type": "text", "text": question})
    return content


def seeing_task(text: str, lang: str) -> str:
    """What the system prompt of a model that sees the images adds: look thoroughly, then answer the
    question, or without one say what the image shows (prompts vision/task_*)."""
    from .prompt_loader import load_prompt

    instruction = (
        load_prompt("vision/task_instruction_question", lang=lang, question=text.strip())
        if text.strip()
        else load_prompt("vision/task_instruction_default", lang=lang)
    )
    return load_prompt("vision/task_adaptive", lang=lang, task_instruction=instruction)


def description_block(description: str, lang: str) -> str:
    """The description as it joins the user turn."""
    from .prompt_loader import load_prompt

    return load_prompt("vision/image_description_block", lang=lang, description=description)


class DescribeSettings(Protocol):
    """What a description reads: the browser state and RuntimeSettings."""

    backend_type: str
    backend_url: str
    enable_tts: bool
    automatik_model_id: str
    agent_tuning: dict[str, AgentTuning]


def tuning_row(settings: DescribeSettings, model: str) -> str:
    """The agent_tuning row whose sampling applies when ``model`` looks at an uploaded image: the
    main model's own ("aifred") or the vision LLM's ("vision")."""
    from .runtime_settings import effective_model_id

    return "aifred" if model == effective_model_id(settings, "aifred") else "vision"


async def describe_images(
    images: list[TurnImage], question: str, lang: str, model: str, settings: DescribeSettings,
) -> str:
    """Describe the images ONCE for agents that answer without seeing them: a main model that
    cannot see, and a turn of several agents (one neutral description all of them discuss, one
    call instead of one per agent). The describer cannot look again for the agent that answers, so
    it captures what the question needs; without a question it hands over neutrally.

    A describer of another machine answers from that machine's llama-swap, under its id there.
    """
    from ..backends.base import LLMOptions
    from .agent_settings import thinking_fields, thinking_mode
    from .llm_client import LLMClient
    from .prompt_loader import load_prompt
    from .remote_describers import BACKEND_TYPE, endpoint
    from .vision_utils import load_image_as_base64

    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{load_image_as_base64(image.path)}"}}
        for image in images
    ]
    instruction = (
        load_prompt("vision/task_instruction_handoff_question", lang=lang, question=question)
        if question
        else load_prompt("vision/task_instruction_handoff", lang=lang)
    )
    content.append({"type": "text", "text": load_prompt("vision/task_adaptive", lang=lang, task_instruction=instruction)})

    # A description is the vision agent's task: it thinks as the vision row says for the model that describes.
    enable_thinking, reasoning_effort = thinking_fields(thinking_mode(settings, "vision", model))
    row = settings.agent_tuning[tuning_row(settings, model)]
    remote = endpoint(model)
    if remote is not None:
        llm_client = LLMClient(backend_type=BACKEND_TYPE, base_url=remote[0])
        model = remote[1]
    else:
        llm_client = LLMClient(backend_type=settings.backend_type, base_url=settings.backend_url)
    try:
        response = await llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": content}],
            options=LLMOptions(
                temperature=row.temperature,
                num_ctx=row.max_context or None,
                enable_thinking=enable_thinking,
                reasoning_effort=reasoning_effort,
            ),
        )
    finally:
        await llm_client.close()
    return response.text.strip()
