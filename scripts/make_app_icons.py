"""Builds the app icons for Android's adaptive launcher icons from the hat (frontend/public/icon-512.png).

* ``icon-maskable-512.png``: the hat on the page colour (theme.py), full bleed, inside the safe zone, so the
  launcher's mask (circle, rounded square) cuts nothing off and no white tile is put behind it.
* ``icon-monochrome-512.png``: only the hat's shape; Android 13+ tints it for themed icons.

Run after the hat changes: ``PYTHONPATH=. venv/bin/python scripts/make_app_icons.py``.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from aifred.theme import COLORS

PUBLIC = Path(__file__).resolve().parents[1] / "frontend" / "public"
SIZE = 512
# Android keeps a circle of 80 % of the icon's width visible under every mask (W3C: maskable icon safe zone).
SAFE_RADIUS = SIZE * 0.4


def fitted_hat() -> Image.Image:
    """The hat, scaled so its bounding box lies inside the safe circle, centred on a transparent square."""
    hat = Image.open(PUBLIC / "icon-512.png").convert("RGBA")
    box = hat.getchannel("A").getbbox()
    assert box is not None, "the hat icon is empty"
    hat = hat.crop(box)
    scale = SAFE_RADIUS / (math.hypot(*hat.size) / 2)
    hat = hat.resize((round(hat.width * scale), round(hat.height * scale)), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(hat, ((SIZE - hat.width) // 2, (SIZE - hat.height) // 2), hat)
    return canvas


def main() -> None:
    hat = fitted_hat()
    page = Image.new("RGBA", (SIZE, SIZE), COLORS["page_bg"])
    page.alpha_composite(hat)
    page.convert("RGB").save(PUBLIC / "icon-maskable-512.png")
    shape = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255))
    shape.putalpha(hat.getchannel("A"))
    shape.save(PUBLIC / "icon-monochrome-512.png")


if __name__ == "__main__":
    main()
