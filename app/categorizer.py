"""Maps file extensions to categories.

This is deliberately just a dictionary of lists — no classes, no config
files, no external data. To add or change a category, edit CATEGORIES below.
"""
from __future__ import annotations

CATEGORIES: dict[str, list[str]] = {
    "Documents": [
        ".pdf", ".doc", ".docx", ".txt", ".rtf",
        ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".odt", ".md",
    ],
    "Images": [
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".heic",
    ],
    "Videos": [
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv",
    ],
    "Audio": [
        ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac",
    ],
    "Archives": [
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ],
    "Applications": [
        ".exe", ".msi", ".dmg", ".pkg", ".apk", ".deb", ".rpm",
    ],
}

# Category used for any extension not listed above (or files with no extension).
FALLBACK_CATEGORY = "Other"

# Build a flat extension -> category lookup once, at import time.
_EXTENSION_TO_CATEGORY: dict[str, str] = {
    ext.lower(): category
    for category, extensions in CATEGORIES.items()
    for ext in extensions
}


def category_for_filename(filename: str) -> str:
    """Return the category name for a given filename, based on its extension.

    Extension matching is case-insensitive ('.PDF' and '.pdf' behave the
    same). Files with multiple dots (e.g. 'archive.tar.gz') are matched on
    their last extension ('.gz'). Files with no extension fall back to
    FALLBACK_CATEGORY.
    """
    # os.path.splitext handles multi-dot filenames correctly by design:
    # it splits on the *last* dot, so "archive.tar.gz" -> ("archive.tar", ".gz").
    import os
    _, ext = os.path.splitext(filename)
    return _EXTENSION_TO_CATEGORY.get(ext.lower(), FALLBACK_CATEGORY)


def all_category_names() -> list[str]:
    """All category names, in a stable display order (fallback category last)."""
    return list(CATEGORIES.keys()) + [FALLBACK_CATEGORY]
