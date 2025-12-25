from __future__ import annotations

import cv2
import numpy as np


def enhance_for_ocr(image: np.ndarray) -> np.ndarray:
    """Enhance cropped label image for OCR.

    This tries to keep numbers/letters while reducing line noise and
    circle outlines common in exploded-view diagrams.
    """
    if image is None or image.size == 0:
        return image

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 3)

    thresh = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        21,
        9,
    )

    # Remove thin lines (guides) while preserving text strokes.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    opened = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)

    # Slight dilation to strengthen text.
    dilated = cv2.dilate(opened, kernel, iterations=1)
    return cv2.cvtColor(dilated, cv2.COLOR_GRAY2BGR)
