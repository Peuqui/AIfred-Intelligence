"""
Image Mixin – handles image upload, crop, lightbox, camera detection, and mobile detection.

Part of the AIfred State refactoring (aifred/state/ package).
"""

import reflex as rx
from typing import Dict, List


class ImageMixin(rx.State, mixin=True):
    """Mixin for image upload, crop, lightbox, help modals, and device detection."""

    # ------------------------------------------------------------------
    # State variables
    # ------------------------------------------------------------------

    # Image Upload State
    is_uploading_image: bool = False
    pending_images: List[Dict[str, str]] = []
    image_upload_warning: str = ""
    max_images_per_message: int = 5
    camera_available: bool = False
    _camera_detection_done: bool = False

    # Image Crop State
    crop_modal_open: bool = False
    crop_image_index: int = -1
    crop_preview_url: str = ""
    crop_box_x: float = 0.0
    crop_box_y: float = 0.0
    crop_box_width: float = 100.0
    crop_box_height: float = 100.0
    crop_rotation: int = 0

    # Image Lightbox State
    lightbox_open: bool = False
    lightbox_image_url: str = ""

    # Help Modals
    multi_agent_help_open: bool = False
    reasoning_thinking_help_open: bool = False
    research_help_open: bool = False
    model_lifecycle_help_open: bool = False

    # Mobile Detection
    is_mobile: bool = False
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

    def on_camera_click(self):
        """Debug message when camera button is clicked"""
        self.add_debug("\U0001f4f7 Opening camera...")  # type: ignore[attr-defined]
        yield

    def on_file_picker_click(self):
        """Debug message when file picker button is clicked"""
        self.add_debug("\U0001f5bc\ufe0f Opening file picker...")  # type: ignore[attr-defined]
        yield

    async def handle_image_upload(self, files: List[rx.UploadFile]):
        """Handle image file uploads - keeps original filename"""
        async for _ in self._process_image_upload(files, from_camera=False):
            yield

    async def handle_camera_upload(self, files: List[rx.UploadFile]):
        """Handle camera uploads - shortens filename to Image_001.jpg"""
        async for _ in self._process_image_upload(files, from_camera=True):
            yield

    async def _process_image_upload(self, files: List[rx.UploadFile], from_camera: bool = False):
        """Internal handler for image uploads with validation (async generator for UI updates)"""
        from ..lib.vision_utils import (
            validate_image_file,
            resize_image_if_needed,
            save_image_to_file,
            get_image_url,
        )

        # Show loading state immediately
        self.is_uploading_image = True

        # Log upload start with file count
        file_count = len(files) if hasattr(files, '__len__') else 1
        source = "camera" if from_camera else "file picker"
        self.add_debug(f"\U0001f4e4 Uploading {file_count} image(s) from {source}...")  # type: ignore[attr-defined]
        yield  # Update UI to show spinner

        try:
            # Check if vision model selected
            if not self.vision_model:  # type: ignore[attr-defined]
                self.image_upload_warning = "\u26a0\ufe0f Please select a Vision model in settings first."
                self.add_debug("\u26a0\ufe0f Image upload blocked: No vision model selected")  # type: ignore[attr-defined]
                return

            # Check if vision_model is in the vision models cache (metadata-validated)
            if self.vision_model_id not in self.vision_models_cache:  # type: ignore[attr-defined]
                self.image_upload_warning = (
                    "\u26a0\ufe0f Selected Vision model doesn't support images. "
                    "Please choose a different Vision model from the dropdown."
                )
                self.add_debug("\u26a0\ufe0f Image upload blocked: Non-vision model selected")  # type: ignore[attr-defined]
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
                valid, error = validate_image_file(filename, len(content))
                if not valid:
                    self.image_upload_warning = error or ""
                    continue

                # Resize if needed (save bandwidth/VRAM)
                resized_content = resize_image_if_needed(content)

                # Camera photos: Short generic name (uuid prefix added by save_image_to_file)
                # File uploads: Keep original filename
                if from_camera:
                    ext = filename.rsplit(".", 1)[1] if "." in filename else "jpg"
                    display_name = f"Image.{ext}"
                else:
                    display_name = filename

                # Save image as file (not Base64 in memory)
                # Uses session_id for persistent session-based storage
                image_path = save_image_to_file(resized_content, self.session_id, display_name)  # type: ignore[attr-defined]
                image_url = get_image_url(image_path)

                # Use actual filename (includes uuid prefix from save_image_to_file)
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
                        "size_kb": str(len(resized_content) // 1024),
                    },
                ]

                self.add_debug(f"\U0001f4f7 Image uploaded: {saved_name} ({len(resized_content) // 1024} KB)")  # type: ignore[attr-defined]
                yield  # Update UI after each image

        finally:
            # Always hide loading state
            self.is_uploading_image = False
            yield  # Update UI to hide spinner

    def remove_pending_image(self, index: int) -> None:
        """Remove image from pending uploads"""
        if 0 <= index < len(self.pending_images):
            removed = self.pending_images.pop(index)
            self.add_debug(f"\U0001f5d1\ufe0f Image removed: {removed['name']}")  # type: ignore[attr-defined]

            # Clear warning if it was about model compatibility
            if self.image_upload_warning.startswith("\u26a0\ufe0f Selected model"):
                self.image_upload_warning = ""

    def clear_pending_images(self) -> None:
        """Clear all pending images"""
        count = len(self.pending_images)
        self.pending_images = []
        self.image_upload_warning = ""
        if count > 0:
            self.add_debug(f"📷 {count} pending image(s) cleared")  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Image Lightbox Handlers
    # ------------------------------------------------------------------

    def open_lightbox(self, image_url: str) -> None:
        """Opens lightbox to view image in full size"""
        self.lightbox_image_url = image_url
        self.lightbox_open = True

    def close_lightbox(self) -> None:
        """Closes the lightbox modal"""
        self.lightbox_open = False
        self.lightbox_image_url = ""

    # ------------------------------------------------------------------
    # Multi-Agent Help Modal Handlers
    # ------------------------------------------------------------------

    def open_multi_agent_help(self) -> None:
        """Opens the multi-agent modes help modal"""
        self.multi_agent_help_open = True

    def close_multi_agent_help(self) -> None:
        """Closes the multi-agent modes help modal"""
        self.multi_agent_help_open = False

    # ------------------------------------------------------------------
    # Research Mode Help Modal Handlers
    # ------------------------------------------------------------------

    def open_research_help(self) -> None:
        """Opens the research modes help modal."""
        self.research_help_open = True

    def close_research_help(self) -> None:
        """Closes the research modes help modal."""
        self.research_help_open = False

    # ------------------------------------------------------------------
    # Reasoning/Thinking Help Modal Handlers
    # ------------------------------------------------------------------

    def open_reasoning_thinking_help(self) -> None:
        """Opens the reasoning/thinking explanation modal"""
        self.reasoning_thinking_help_open = True

    def close_reasoning_thinking_help(self) -> None:
        """Closes the reasoning/thinking explanation modal"""
        self.reasoning_thinking_help_open = False

    # ------------------------------------------------------------------
    # Model Lifecycle Help Modal Handlers
    # ------------------------------------------------------------------

    def open_model_lifecycle_help(self) -> None:
        """Opens the model lifecycle (base/VLM/TTS/LLM) help modal."""
        self.model_lifecycle_help_open = True

    def close_model_lifecycle_help(self) -> None:
        """Closes the model lifecycle help modal."""
        self.model_lifecycle_help_open = False

    # ------------------------------------------------------------------
    # Image Crop Handlers
    # ------------------------------------------------------------------

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
            self.add_debug(f"\u2702\ufe0f Crop mode opened for: {self.pending_images[index]['name']}")  # type: ignore[attr-defined]

    def cancel_crop(self) -> None:
        """Schliesst Modal ohne Aenderung"""
        self.crop_modal_open = False
        self.crop_image_index = -1
        self.crop_preview_url = ""
        self.crop_rotation = 0

    def rotate_crop_image_left(self) -> None:
        """Rotate image 90 degrees counter-clockwise in crop preview"""
        self._rotate_crop_image(clockwise=False)

    def rotate_crop_image_right(self) -> None:
        """Rotate image 90 degrees clockwise in crop preview"""
        self._rotate_crop_image(clockwise=True)

    def _rotate_crop_image(self, clockwise: bool = True) -> None:
        """Internal: Rotate image 90 degrees in crop preview"""
        from PIL import Image
        from pathlib import Path

        if self.crop_image_index < 0 or self.crop_image_index >= len(self.pending_images):
            return

        image_data = self.pending_images[self.crop_image_index]
        image_path = Path(image_data.get("path", ""))

        if not image_path.exists():
            self.add_debug("\u274c Rotate failed: Image file not found")  # type: ignore[attr-defined]
            return

        try:
            img = Image.open(image_path)

            if clockwise:
                img_rotated = img.transpose(Image.Transpose.ROTATE_270)
                rotation_delta = 90
                direction = "\u21bb"
            else:
                img_rotated = img.transpose(Image.Transpose.ROTATE_90)
                rotation_delta = -90
                direction = "\u21ba"

            format_to_use = img.format if img.format in ['JPEG', 'PNG', 'GIF', 'WEBP', 'BMP'] else 'JPEG'
            img_rotated.save(image_path, format=format_to_use, quality=90)

            new_size_kb = image_path.stat().st_size // 1024

            # Cache-busting timestamp so the browser refetches the rotated file
            import time
            cache_buster = f"?t={int(time.time() * 1000)}"
            base_url = image_data["url"].split("?")[0]
            new_url = f"{base_url}{cache_buster}"

            self.crop_preview_url = new_url

            # Reassign the whole list with a fresh dict at the updated index
            # so Reflex's reactivity picks up the nested-dict change.
            idx = self.crop_image_index
            self.pending_images = [
                *self.pending_images[:idx],
                {**self.pending_images[idx], "size_kb": str(new_size_kb), "url": new_url},
                *self.pending_images[idx + 1:],
            ]

            # Track cumulative rotation
            self.crop_rotation = (self.crop_rotation + rotation_delta) % 360

            # Reset crop box (dimensions may have changed)
            self.crop_box_x = 0.0
            self.crop_box_y = 0.0
            self.crop_box_width = 100.0
            self.crop_box_height = 100.0

            self.add_debug(f"\U0001f504 Image rotated {direction} 90\u00b0")  # type: ignore[attr-defined]

        except (ValueError, FileNotFoundError, AttributeError) as e:
            self.add_debug(f"\u274c Rotate failed: {e}")  # type: ignore[attr-defined]

    def update_crop_box(self, x: float, y: float, width: float, height: float) -> None:
        """Update Crop-Box Koordinaten (von JavaScript/UI)"""
        self.crop_box_x = max(0, min(100, x))
        self.crop_box_y = max(0, min(100, y))
        self.crop_box_width = max(1, min(100 - self.crop_box_x, width))
        self.crop_box_height = max(1, min(100 - self.crop_box_y, height))

    async def apply_crop(self) -> None:
        """Applies crop and updates the image in pending_images (Legacy, uses State coordinates)"""
        await self._do_apply_crop(self.crop_box_x, self.crop_box_y, self.crop_box_width, self.crop_box_height)

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
            self.add_debug(f"\u274c Crop failed: {e}")  # type: ignore[attr-defined]
            self.cancel_crop()

    async def _do_apply_crop(self, x: float, y: float, width: float, height: float) -> None:
        """Interne Funktion: Fuehrt den Crop mit gegebenen Koordinaten aus"""
        from ..lib.vision_utils import crop_and_resize_image, save_image_to_file, get_image_url
        from pathlib import Path

        if self.crop_image_index < 0 or self.crop_image_index >= len(self.pending_images):
            self.add_debug("\u274c Crop failed: Invalid image index")  # type: ignore[attr-defined]
            self.cancel_crop()
            return

        image_data = self.pending_images[self.crop_image_index]

        # Read original bytes from file
        try:
            image_path = Path(image_data["path"])
            with open(image_path, 'rb') as f:
                original_bytes = f.read()
        except FileNotFoundError as e:
            self.add_debug(f"\u274c Crop failed: {e}")  # type: ignore[attr-defined]
            self.cancel_crop()
            return

        from PIL import Image
        import io
        original_img = Image.open(io.BytesIO(original_bytes))
        orig_width, orig_height = original_img.size  # noqa: F841

        # Crop anwenden (nur wenn nicht 100%)
        if x > 0.5 or y > 0.5 or width < 99.5 or height < 99.5:
            crop_box = {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
            cropped_bytes = crop_and_resize_image(original_bytes, crop_box=crop_box)

            cropped_img = Image.open(io.BytesIO(cropped_bytes))
            px_width, px_height = cropped_img.size

            # Save cropped image as new file
            new_path = save_image_to_file(cropped_bytes, self.session_id, image_data["name"])  # type: ignore[attr-defined]
            new_url = get_image_url(new_path)

            # Delete old file
            try:
                image_path.unlink()
            except OSError:
                pass

            # Update pending_images with the new file (full reassign so
            # Reflex notices the change at the nested-list level).
            idx = self.crop_image_index
            self.pending_images = [
                *self.pending_images[:idx],
                {
                    "name": image_data["name"],
                    "path": str(new_path),
                    "url": new_url,
                    "size_kb": str(len(cropped_bytes) // 1024),
                },
                *self.pending_images[idx + 1:],
            ]

            self.add_debug(f"\u2702\ufe0f Image cropped: {width:.0f}% x {height:.0f}% \u2192 {px_width} x {px_height} px")  # type: ignore[attr-defined]
        else:
            self.add_debug(f"\u2139\ufe0f No crop needed: {image_data['name']}")  # type: ignore[attr-defined]

        # Close modal
        self.cancel_crop()

    # ------------------------------------------------------------------
    # Device Detection
    # ------------------------------------------------------------------

    def set_camera_available(self, available: bool) -> None:
        """Set camera availability based on browser capabilities (called from JavaScript)"""
        if self._camera_detection_done:
            return

        self.camera_available = available
        self._camera_detection_done = True

        if available:
            self.add_debug("\U0001f4f7 Browser supports camera access")  # type: ignore[attr-defined]
        else:
            self.add_debug("\u26a0\ufe0f Browser does not support camera access")  # type: ignore[attr-defined]

    def set_is_mobile(self, is_mobile: bool) -> None:
        """Set mobile device detection based on User-Agent and touch capabilities (called from JavaScript)"""
        if self._mobile_detection_done:
            return

        self.is_mobile = is_mobile
        self._mobile_detection_done = True

        device_type = "\U0001f4f1 Mobile" if is_mobile else "\U0001f5a5\ufe0f Desktop"
        self.add_debug(f"{device_type} device detected")  # type: ignore[attr-defined]

        # Add separator after browser/device detection (marks end of startup)
        from ..lib.logging_utils import console_separator
        console_separator()  # File log
        self.debug_messages = [*self.debug_messages, "\u2500" * 20]  # type: ignore[attr-defined]  # UI
