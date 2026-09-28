"""Vision PNG encoder for drawing-llmextractor.

``chat._prepare_image_base64`` shrinks every image over 2_000_000 pixels.
That cap is for older vision calls. This skill sends crops through Bedrock
Converse (Astra ``original`` / ``auto``): each image may be up to 8000px on
a side and 3.75 MB, and Astra rejects a request above 30,000 patches of
32×32 instead of downscaling it to fit.
"""

from __future__ import annotations

import base64
import logging
import math
from io import BytesIO

from PIL import Image

logger = logging.getLogger("drawing-llmextractor")

# Bedrock Converse: each image ≤ 3.75 MB, 8000 × 8000.
# Astra original/auto: 32px patches, reject above 30_000 (no auto-shrink).
MAX_SIDE = 8000
PATCH = 32
MAX_PATCHES = 30_000
MAX_IMAGE_BYTES = int(3.75 * 1024 * 1024)


def patch_count(width: int, height: int) -> int:
    if width <= 0 or height <= 0:
        return 0
    return math.ceil(width / PATCH) * math.ceil(height / PATCH)


def within_vision_limits(width: int, height: int) -> bool:
    return (
        width <= MAX_SIDE
        and height <= MAX_SIDE
        and patch_count(width, height) <= MAX_PATCHES
    )


def encode_vision_png(image: Image.Image) -> str:
    """Return a base64 PNG that fits Bedrock Converse and Astra patch limits.

    Does not apply the old 2_000_000-pixel shrink. A crop is scaled down only
    when the PNG exceeds 3.75 MB, or when width, height, or patch count is
    still over the limit.
    """
    img = image
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

    width, height = img.size
    logger.info(
        "tile image %sx%s (%s px, %s patches)",
        width,
        height,
        width * height,
        patch_count(width, height),
    )

    while width > 1 and height > 1 and not within_vision_limits(width, height):
        width = max(1, int(width * 0.8))
        height = max(1, int(height * 0.8))
        img = img.resize((width, height))
        logger.info("resized to %sx%s to fit vision limits", width, height)

    img_base64 = ""
    for attempt in range(5):
        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        img_bytes = buffer.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")
        logger.info(
            "tile encode attempt %s: png=%s bytes base64=%s",
            attempt + 1,
            len(img_bytes),
            len(img_base64),
        )
        if len(img_bytes) <= MAX_IMAGE_BYTES:
            return img_base64
        width = max(1, int(width * 0.8))
        height = max(1, int(height * 0.8))
        img = img.resize((width, height))
        logger.info("resized to %sx%s because png exceeds 3.75MB", width, height)

    raise RuntimeError("조각 PNG가 3.75MB 이하로 줄지 않습니다.")
