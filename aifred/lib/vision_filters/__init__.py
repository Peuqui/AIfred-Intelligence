"""Vision-Filter & Analyzer — Pipeline-Stufen zwischen Frame-Source und Event-Store.

Drei Stufen, jede stateful pro Source:

* ``motion``      — cv2 BackgroundSubtractor; cheapest Vorfilter, läuft auf jedem Frame
* ``face_detect`` — InsightFace RetinaFace; läuft wenn Motion getriggert hat
* ``face_recognize`` — Cosine-Match der Detection-Embeddings gegen die in
  ``vision_store.face_embeddings`` registrierten Personen

VLM-Analyse (Bild → Beschreibungstext) liegt eine Ebene höher in
``aifred.lib.vision_analyzer`` und wird nur on-event aufgerufen.
"""

from __future__ import annotations

from .motion import MotionDetector, MotionResult

__all__ = ["MotionDetector", "MotionResult"]
