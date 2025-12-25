from __future__ import annotations

import re

ALLOWED_PATTERN = re.compile(r"[^A-Z0-9-]")


def normalize_label(text: str) -> str:
    if not text:
        return text

    cleaned = text.strip().upper()
    cleaned = cleaned.replace("–", "-").replace("—", "-").replace("·", "-")
    cleaned = cleaned.replace("_", "-")
    cleaned = cleaned.replace(" ", "")
    cleaned = ALLOWED_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)

    if not cleaned:
        return text.strip()

    # Heuristic: map letter O to 0 if string is numeric-like.
    if re.fullmatch(r"[0-9O-]+", cleaned):
        cleaned = cleaned.replace("O", "0")

    return cleaned
