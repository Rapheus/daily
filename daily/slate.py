from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from .config import SlateConfig


class SlateGenerator:
    """Fits a slate image frame onto the output canvas.

    fit="horizontal": scale so width matches canvas; black bars top/bottom if needed.
    fit="vertical":   scale so height matches canvas; black bars left/right if needed.
    """

    def generate(self, config: SlateConfig, output_size: tuple[int, int]) -> np.ndarray:
        canvas_w, canvas_h = output_size
        canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.float32)

        if not config.frame_path or not config.frame_path.exists():
            return canvas

        img = Image.open(str(config.frame_path)).convert("RGB")
        img_w, img_h = img.size

        if config.fit == "horizontal":
            scale = canvas_w / img_w
        else:
            scale = canvas_h / img_h

        new_w = round(img_w * scale)
        new_h = round(img_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        x = (canvas_w - new_w) // 2
        y = (canvas_h - new_h) // 2

        arr = np.array(img).astype(np.float32) / 255.0
        # Destination region on canvas (clamped to canvas bounds)
        y1, y2 = max(0, y), min(canvas_h, y + new_h)
        x1, x2 = max(0, x), min(canvas_w, x + new_w)
        # Corresponding source region (offset by how much was clipped on each edge)
        sy1, sx1 = y1 - y, x1 - x
        canvas[y1:y2, x1:x2] = arr[sy1:sy1 + (y2 - y1), sx1:sx1 + (x2 - x1)]

        return canvas
