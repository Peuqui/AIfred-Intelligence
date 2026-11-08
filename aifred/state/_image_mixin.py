"""
Image Mixin – handles image upload, crop, camera detection, and mobile detection.

Part of the AIfred State refactoring (aifred/state/ package).
"""

import asyncio
from typing import TYPE_CHECKING

import reflex as rx

from ..lib.config import MAX_IMAGES_PER_MESSAGE
from ..lib.turn_images import TurnImage

if TYPE_CHECKING:
    from ..lib.agent_tuning import AgentTuning


class ImageMixin(rx.State, mixin=True):
    """Mixin for image upload, crop, help modals, and device detection."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        agent_tuning: rx.Field[dict[str, AgentTuning]]
        debug_messages: rx.Field[list[str]]
        logged_in_user: rx.Field[str]
        session_id: rx.Field[str]
        ui_language: rx.Field[str]
        vision_models_cache: rx.Field[list[str]]

        def _chat_sub(self): ...
        @rx.event
        def add_debug(self, message: str) -> None: ...

    # ------------------------------------------------------------------
    # State variables
    # ------------------------------------------------------------------

    # Image Upload State
    is_uploading_image: rx.Field[bool] = rx.field(False)
    pending_images: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)
    image_upload_warning: rx.Field[str] = rx.field("")
    max_images_per_message: rx.Field[int] = rx.field(MAX_IMAGES_PER_MESSAGE)
    camera_available: rx.Field[bool] = rx.field(False)
    _camera_detection_done: bool = False

    # Image Crop State
    crop_modal_open: rx.Field[bool] = rx.field(False)
    crop_image_index: rx.Field[int] = rx.field(-1)
    crop_preview_url: rx.Field[str] = rx.field("")
    crop_box_x: rx.Field[float] = rx.field(0.0)
    crop_box_y: rx.Field[float] = rx.field(0.0)
    crop_box_width: rx.Field[float] = rx.field(100.0)
    crop_box_height: rx.Field[float] = rx.field(100.0)
    crop_rotation: rx.Field[int] = rx.field(0)

    # Help Modals
    multi_agent_help_open: rx.Field[bool] = rx.field(False)
    reasoning_thinking_help_open: rx.Field[bool] = rx.field(False)
    model_lifecycle_help_open: rx.Field[bool] = rx.field(False)

    # Mobile Detection
    is_mobile: rx.Field[bool] = rx.field(False)
    _mobile_detection_done: bool = False

    # ------------------------------------------------------------------
    # URL normalisation (image-related)
    # ------------------------------------------------------------------

    def _normalize_upload_urls(self) -> None:
        """
        Konvertiert absolute URLs in chat_history zu relativen URLs.

        Behebt das Problem, dass Sessions die auf einem Port erstellt wurden
        (z.B. 8443) nicht korrekt von einem anderen Port (z.B. 443) aus
        geladen werden können.

        Pattern: http(s)://host:port/_upload/... -> /_upload/...
        """
        import re

        for msg in self._chat_sub().chat_history:
            # Skip invalid messages
            if not isinstance(msg, dict):
                continue

            # 1. Normalisiere URLs im content (HTML)
            content = msg.get("content")
            if content and isinstance(content, str):
                msg["content"] = re.sub(
                    r'https?://[^/]+/_upload/',
                    '/_upload/',
                    content,
                )

            # 2. Normalisiere URLs in metadata.images
            images = msg.get("metadata", {}).get("images")
            if images and isinstance(images, list):
                for img in images:
                    if isinstance(img, dict):
                        url = img.get("url")
                        if url and isinstance(url, str):
                            img["url"] = re.sub(
                                r'https?://[^/]+/_upload/',
                                '/_upload/',
                                url,
                            )

    # ------------------------------------------------------------------
    # Image Upload Handlers
    # ------------------------------------------------------------------

    @rx.event
    def on_camera_click(self):
        """Debug message when camera button is clicked"""
        self.add_debug("\U0001f4f7 Opening camera...")
        yield

    @rx.event
    def on_file_picker_click(self):
        """Debug message when file picker button is clicked"""
        self.add_debug("\U0001f5bc\ufe0f Opening file picker...")
        yield

    @rx.event
    async def handle_image_upload(self, files: list[rx.UploadFile]):
        """Handle image file uploads - keeps original filename"""
        async for _ in self._process_image_upload(files, from_camera=False):
            yield

    @rx.event
    async def handle_camera_upload(self, files: list[rx.UploadFile]):
        """Handle camera uploads - shortens filename to Image_001.jpg"""
        async for _ in self._process_image_upload(files, from_camera=True):
            yield

    async def _process_image_upload(self, files: list[rx.UploadFile], from_camera: bool = False):
        """Internal handler for image uploads with validation (async generator for UI updates)"""
        from ..lib.user_error import Invalid, said
        from ..lib.vision_utils import check_image_file, get_image_url, save_session_upload

        # POST /_upload reaches this handler with any client token, a fresh one gives a state
        # without login: nothing may touch the disk before this check.
        if not self.logged_in_user:
            self.add_debug("⚠️ Image upload rejected: not logged in")
            return

        # Show loading state immediately
        self.is_uploading_image = True

        # Log upload start with file count
        file_count = len(files) if hasattr(files, '__len__') else 1
        source = "camera" if from_camera else "file picker"
        self.add_debug(f"\U0001f4e4 Uploading {file_count} image(s) from {source}...")
        yield  # Update UI to show spinner

        try:
            # Check if vision model selected
            if not self.agent_tuning["vision"].model:
                self.image_upload_warning = "\u26a0\ufe0f Please select a Vision model in settings first."
                self.add_debug("\u26a0\ufe0f Image upload blocked: No vision model selected")
                return

            # Check if vision_model is in the vision models cache (metadata-validated)
            if self.agent_tuning["vision"].model_id not in self.vision_models_cache:
                self.image_upload_warning = (
                    "\u26a0\ufe0f Selected Vision model doesn't support images. "
                    "Please choose a different Vision model from the dropdown."
                )
                self.add_debug("\u26a0\ufe0f Image upload blocked: Non-vision model selected")
                return

            # Clear previous warning
            self.image_upload_warning = ""

            # Check max images limit
            if len(self.pending_images) + len(files) > self.max_images_per_message:
                self.image_upload_warning = f"\u26a0\ufe0f Maximum {self.max_images_per_message} images per message"
                return

            for file in files:
                # Read file content
                content = await file.read()

                # Validate
                filename = file.filename or "unknown.jpg"
                try:
                    check_image_file(filename)
                except Invalid as refused:  # a format the upload does not take: the next file goes on
                    self.image_upload_warning = f"⚠️ {said(refused, self.ui_language)}"
                    continue

                # Resized, unique name (original stem + timestamp; camera shots
                # have no meaningful name → "camera"), under the user-upload
                # tree data/upload/images/<session>/ — SSOT save_session_upload.
                ext = filename.rsplit(".", 1)[1].lower() if "." in filename else "jpg"
                stem = "camera" if from_camera else (filename.rsplit(".", 1)[0] if "." in filename else filename)
                image_path = save_session_upload(content, self.session_id, stem, ext)
                image_url = get_image_url(image_path)
                saved_name = image_path.name

                # Store with file path (for LLM) and URL (for UI).
                # Reassign instead of .append() — Reflex only reacts to
                # identity changes on State containers.
                self.pending_images = [
                    *self.pending_images,
                    {
                        "name": saved_name,
                        "path": str(image_path),
                        "url": image_url,
                        "size_kb": str(image_path.stat().st_size // 1024),
                    },
                ]

                self.add_debug(f"\U0001f4f7 Image uploaded: {saved_name} ({image_path.stat().st_size // 1024} KB)")
                yield  # Update UI after each image

        finally:
            # Always hide loading state
            self.is_uploading_image = False
            yield  # Update UI to hide spinner

    @rx.event
    def remove_pending_image(self, index: int) -> None:
        """Remove image from pending uploads"""
        if 0 <= index < len(self.pending_images):
            removed = self.pending_images.pop(index)
            self.add_debug(f"\U0001f5d1\ufe0f Image removed: {removed['name']}")

            # Clear warning if it was about model compatibility
            if self.image_upload_warning.startswith("\u26a0\ufe0f Selected model"):
                self.image_upload_warning = ""

    @rx.event
    def clear_pending_images(self) -> None:
        """Clear all pending images"""
        count = len(self.pending_images)
        self.pending_images = []
        self.image_upload_warning = ""
        if count > 0:
            self.add_debug(f"📷 {count} pending image(s) cleared")

    # ------------------------------------------------------------------
    # Multi-Agent Help Modal Handlers
    # ------------------------------------------------------------------

    @rx.event
    def open_multi_agent_help(self) -> None:
        """Opens the multi-agent modes help modal"""
        self.multi_agent_help_open = True

    @rx.event
    def close_multi_agent_help(self) -> None:
        """Closes the multi-agent modes help modal"""
        self.multi_agent_help_open = False

    # ------------------------------------------------------------------
    # Reasoning/Thinking Help Modal Handlers
    # ------------------------------------------------------------------

    @rx.event
    def open_reasoning_thinking_help(self) -> None:
        """Opens the reasoning/thinking explanation modal"""
        self.reasoning_thinking_help_open = True

    @rx.event
    def close_reasoning_thinking_help(self) -> None:
        """Closes the reasoning/thinking explanation modal"""
        self.reasoning_thinking_help_open = False

    # ------------------------------------------------------------------
    # Model Lifecycle Help Modal Handlers
    # ------------------------------------------------------------------

    @rx.event
    def open_model_lifecycle_help(self) -> None:
        """Opens the model lifecycle (base/VLM/TTS/LLM) help modal."""
        self.model_lifecycle_help_open = True

    @rx.event
    def close_model_lifecycle_help(self) -> None:
        """Closes the model lifecycle help modal."""
        self.model_lifecycle_help_open = False

    # ------------------------------------------------------------------
    # Image Crop Handlers
    # ------------------------------------------------------------------

    @rx.event
    def open_crop_modal(self, index: int) -> None:
        """Opens crop modal for image at index"""
        if 0 <= index < len(self.pending_images):
            self.crop_image_index = index
            self.crop_preview_url = self.pending_images[index]["url"]
            # Reset crop box to full image
            self.crop_box_x = 0.0
            self.crop_box_y = 0.0
            self.crop_box_width = 100.0
            self.crop_box_height = 100.0
            self.crop_rotation = 0
            self.crop_modal_open = True
            self.add_debug(f"\u2702\ufe0f Crop mode opened for: {self.pending_images[index]['name']}")

    @rx.event
    def cancel_crop(self) -> None:
        """Schliesst Modal ohne Aenderung"""
        self.crop_modal_open = False
        self.crop_image_index = -1
        self.crop_preview_url = ""
        self.crop_rotation = 0

    @rx.event
    def rotate_crop_image_left(self) -> None:
        """Rotate image 90 degrees counter-clockwise in crop preview"""
        self._rotate_crop_image(clockwise=False)

    @rx.event
    def rotate_crop_image_right(self) -> None:
        """Rotate image 90 degrees clockwise in crop preview"""
        self._rotate_crop_image(clockwise=True)

    def _rotate_crop_image(self, clockwise: bool = True) -> None:
        """Internal: Rotate image 90 degrees in crop preview (rule: ``turn_images.rotate_upload``)."""
        from ..lib.turn_images import rotate_upload

        if self.crop_image_index < 0 or self.crop_image_index >= len(self.pending_images):
            return
        try:
            turned = rotate_upload(self.session_id, self._crop_target(), clockwise)
        except OSError as e:
            self.add_debug(f"\u274c Rotate failed: {e}")
            return
        self._replace_pending_image(turned)
        self.crop_preview_url = turned.url
        self.crop_rotation = (self.crop_rotation + (90 if clockwise else -90)) % 360
        # Reset crop box (dimensions may have changed)
        self.crop_box_x = 0.0
        self.crop_box_y = 0.0
        self.crop_box_width = 100.0
        self.crop_box_height = 100.0
        arrow = "\u21bb" if clockwise else "\u21ba"
        self.add_debug(f"\U0001f504 Image rotated {arrow} 90\u00b0")

    def _crop_target(self) -> TurnImage:
        """The pending image the crop modal works on."""
        from pathlib import Path

        image_data = self.pending_images[self.crop_image_index]
        return TurnImage(image_data["name"], image_data["url"], Path(image_data["path"]))

    def _replace_pending_image(self, image: TurnImage) -> None:
        """The pending image of the crop modal is now ``image`` (a new file). The whole list is
        reassigned so Reflex notices the change at the nested-list level."""
        idx = self.crop_image_index
        self.pending_images = [
            *self.pending_images[:idx],
            {"name": image.name, "path": str(image.path), "url": image.url, "size_kb": str(image.path.stat().st_size // 1024)},
            *self.pending_images[idx + 1:],
        ]

    @rx.event
    async def apply_crop_with_coords(self, coords_json: str) -> None:
        """Applies crop with coordinates from JavaScript (JSON String)"""
        import json
        try:
            coords = json.loads(coords_json)
            x = float(coords.get("x", 0))
            y = float(coords.get("y", 0))
            width = float(coords.get("width", 100))
            height = float(coords.get("height", 100))
            await self._do_apply_crop(x, y, width, height)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            self.add_debug(f"\u274c Crop failed: {e}")
            self.cancel_crop()

    async def _do_apply_crop(self, x: float, y: float, width: float, height: float) -> None:
        """Interne Funktion: Führt den Crop mit gegebenen Koordinaten aus (Regel: ``turn_images.crop_upload``)."""
        from ..lib.formatting import format_number
        from ..lib.turn_images import crop_upload

        if self.crop_image_index < 0 or self.crop_image_index >= len(self.pending_images):
            self.add_debug("\u274c Crop failed: Invalid image index")
            self.cancel_crop()
            return
        image = self._crop_target()
        try:
            cropped = await asyncio.to_thread(
                crop_upload, self.session_id, image, {"x": x, "y": y, "width": width, "height": height},
            )
        except OSError as e:
            self.add_debug(f"\u274c Crop failed: {e}")
            self.cancel_crop()
            return
        if cropped is image:
            self.add_debug(f"\u2139\ufe0f No crop needed: {image.name}")
        else:
            self._replace_pending_image(cropped)
            self.add_debug(f"\u2702\ufe0f Image cropped: {format_number(width, 0)}% x {format_number(height, 0)}%")
        self.cancel_crop()

    # ------------------------------------------------------------------
    # Device Detection
    # ------------------------------------------------------------------

    @rx.event
    def set_camera_available(self, available: bool) -> None:
        """Set camera availability based on browser capabilities (called from JavaScript)"""
        if self._camera_detection_done:
            return

        self.camera_available = available
        self._camera_detection_done = True

        if available:
            self.add_debug("\U0001f4f7 Browser supports camera access")
        else:
            self.add_debug("\u26a0\ufe0f Browser does not support camera access")

    @rx.event
    def set_is_mobile(self, is_mobile: bool) -> None:
        """Set mobile device detection based on User-Agent and touch capabilities (called from JavaScript)"""
        if self._mobile_detection_done:
            return

        self.is_mobile = is_mobile
        self._mobile_detection_done = True

        device_type = "\U0001f4f1 Mobile" if is_mobile else "\U0001f5a5\ufe0f Desktop"
        self.add_debug(f"{device_type} device detected")

        # Add separator after browser/device detection (marks end of startup)
        from ..lib.logging_utils import console_separator
        console_separator()  # File log
        self.debug_messages = [*self.debug_messages, "\u2500" * 20]  # UI
