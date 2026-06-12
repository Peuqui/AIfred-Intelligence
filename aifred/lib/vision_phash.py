"""Perceptual Hashing für die Bulk-VLM-Analyse-Dedup.

pHash (DCT-basiert) liefert für visuell ähnliche Bilder ähnliche
64-bit Hashes — Hamming-Distanz ≤ 5 = „praktisch gleich". Reicht für
„Person sitzt 5 Minuten am Schreibtisch" → ~600 ähnliche Frames
werden auf einen Repräsentanten reduziert.

Implementiert direkt mit cv2+numpy (keine extra Lib), weil
``imagehash`` nicht im venv ist und der Algorithmus trivial.
"""

from __future__ import annotations

import cv2
import numpy as np


def phash_bytes(image_bytes: bytes) -> int:
    """64-bit pHash für JPEG/PNG-Bytes.

    Algorithmus:
    1. Decode → Graustufen → 32×32 resize
    2. DCT, obere 8×8 Koeffizienten extrahieren (low frequencies)
    3. Median der 8×8 (ohne DC-Koeffizient)
    4. Bits: 1 wenn Koeffizient > Median, sonst 0 → 64 bits

    Returnt einen Python-int (64 bit). Bei nicht-dekodierbarem Bild:
    ``0`` als Sentinel (caller behandelt das wie „kein Match").
    """
    if not image_bytes:
        return 0
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0
    resized = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
    # DCT auf float32
    dct = cv2.dct(resized.astype(np.float32))
    # Obere 8×8 = low frequencies (= dominante Bild-Inhalte)
    low = np.asarray(dct, dtype=np.float32)[:8, :8]
    # DC (oben links) rausnehmen → würde die Helligkeit dominieren
    flat = low.flatten()
    median = float(np.median(flat[1:]))
    bits = 0
    for i, v in enumerate(flat):
        if v > median:
            bits |= 1 << i
    return int(bits)


def phash_file(path: str) -> int:
    """pHash direkt aus einer Datei lesen."""
    with open(path, "rb") as f:
        return phash_bytes(f.read())


def hamming_distance(a: int, b: int) -> int:
    """Anzahl unterschiedlicher Bits zwischen zwei 64-bit Hashes.
    Python 3.10+ hat ``int.bit_count()`` — schneller als bin().count('1').
    """
    return int(a ^ b).bit_count()


def is_similar(a: int, b: int, threshold: int = 5) -> bool:
    """True wenn Hamming-Distanz ≤ threshold. Threshold 5 ist eine
    pragmatische Wahl: tolerant gegen JPEG-Kompression + Mikro-Bewegung,
    streng genug um „andere Szene" zu erkennen."""
    if a == 0 or b == 0:
        # Sentinel — wir wollen ungültige Hashes nicht clustern.
        return False
    return hamming_distance(a, b) <= threshold
