"""Core File Organizer logic: scanning a folder, building a move plan,
executing it safely, and undoing the most recent operation.

This module has no dependency on Flask or any UI — it's plain Python so it
can be tested and reasoned about on its own.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from app.categorizer import category_for_filename

# Files/folders we never touch, even if present directly in the target folder.
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


class InvalidFolderError(Exception):
    """Raised when the given path is not a usable, accessible directory."""


@dataclass
class PlannedMove:
    filename: str          # original filename, e.g. "report.pdf"
    category: str          # destination category, e.g. "Documents"
    dest_filename: str     # possibly renamed to avoid a collision, e.g. "report (1).pdf"


@dataclass
class ScanResult:
    folder: str
    moves: list[PlannedMove] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)

    @property
    def total_files(self) -> int:
        return len(self.moves)

    def counts_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for move in self.moves:
            counts[move.category] = counts.get(move.category, 0) + 1
        return counts


@dataclass
class MoveOutcome:
    filename: str
    category: str
    dest_filename: str
    success: bool
    error: str | None = None


@dataclass
class OrganizeResult:
    folder: str
    outcomes: list[MoveOutcome] = field(default_factory=list)

    @property
    def succeeded(self) -> list[MoveOutcome]:
        return [o for o in self.outcomes if o.success]

    @property
    def failed(self) -> list[MoveOutcome]:
        return [o for o in self.outcomes if not o.success]


def _validate_folder(folder: str) -> str:
    """Return a normalized absolute path, or raise InvalidFolderError."""
    if not folder or not folder.strip():
        raise InvalidFolderError("No folder was selected.")

    path = os.path.abspath(os.path.expanduser(folder.strip()))

    if not os.path.exists(path):
        raise InvalidFolderError(f"This folder does not exist: {path}")
    if not os.path.isdir(path):
        raise InvalidFolderError(f"This is not a folder: {path}")
    if not os.access(path, os.R_OK | os.W_OK):
        raise InvalidFolderError(f"This folder could not be accessed: {path}")

    return path


def scan_folder(folder: str) -> ScanResult:
    """Scan `folder` (non-recursively) and build a plan of proposed moves.

    Does not touch the filesystem beyond reading the directory listing and
    checking which destination filenames already exist (to plan safe,
    non-colliding names up front). No files are moved by this function.
    """
    path = _validate_folder(folder)
    result = ScanResult(folder=path)

    # Track filenames we've already *planned* to place in each category, so
    # that scanning two same-named files in one pass also avoids collisions
    # with each other (not just with files already on disk).
    planned_names_by_category: dict[str, set[str]] = {}

    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError as e:
        raise InvalidFolderError(f"This folder could not be read: {e}") from e

    for entry in entries:
        name = entry.name

        if name in IGNORED_NAMES:
            continue
        if name.startswith("."):
            result.skipped.append((name, "hidden file"))
            continue

        try:
            is_symlink = entry.is_symlink()
        except OSError:
            result.skipped.append((name, "could not be inspected"))
            continue

        if is_symlink:
            result.skipped.append((name, "symbolic link (not moved, to avoid surprises)"))
            continue

        try:
            is_file = entry.is_file()
        except OSError:
            result.skipped.append((name, "could not be inspected"))
            continue

        if not is_file:
            # Directories (and anything else non-file) are left alone.
            continue

        category = category_for_filename(name)
        category_dir = os.path.join(path, category)
        existing = planned_names_by_category.setdefault(category, _existing_names(category_dir))

        dest_name = _safe_destination_name(name, existing)
        existing.add(dest_name)

        result.moves.append(PlannedMove(filename=name, category=category, dest_filename=dest_name))

    return result


def _existing_names(category_dir: str) -> set[str]:
    if not os.path.isdir(category_dir):
        return set()
    try:
        return set(os.listdir(category_dir))
    except OSError:
        return set()


def _safe_destination_name(filename: str, existing_names: set[str]) -> str:
    """Return `filename`, or 'name (1).ext', 'name (2).ext', ... if needed to
    avoid colliding with a name already in `existing_names`.
    """
    if filename not in existing_names:
        return filename

    base, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = f"{base} ({counter}){ext}"
        if candidate not in existing_names:
            return candidate
        counter += 1


def organize(scan_result: ScanResult) -> OrganizeResult:
    """Execute a previously-built ScanResult: create category folders and
    move each planned file. Never overwrites an existing file, never
    deletes anything, and continues past individual failures.
    """
    folder = scan_result.folder
    result = OrganizeResult(folder=folder)

    for move in scan_result.moves:
        src = os.path.join(folder, move.filename)
        category_dir = os.path.join(folder, move.category)
        dest = os.path.join(category_dir, move.dest_filename)

        try:
            if not os.path.isfile(src):
                # The file disappeared (or was already moved) since scanning.
                raise FileNotFoundError(f"'{move.filename}' no longer exists.")

            os.makedirs(category_dir, exist_ok=True)

            if os.path.exists(dest):
                # Defensive re-check: something else claimed this name since
                # the scan (e.g. a concurrent run). Never overwrite — re-plan
                # a fresh safe name instead of failing outright.
                dest = os.path.join(category_dir, _safe_destination_name(move.dest_filename, set(os.listdir(category_dir))))

            shutil.move(src, dest)
            result.outcomes.append(MoveOutcome(
                filename=move.filename, category=move.category,
                dest_filename=os.path.basename(dest), success=True,
            ))
        except OSError as e:
            result.outcomes.append(MoveOutcome(
                filename=move.filename, category=move.category,
                dest_filename=move.dest_filename, success=False, error=str(e),
            ))

    return result


def undo(organize_result: OrganizeResult) -> OrganizeResult:
    """Move every successfully-moved file back to its original location.

    Only attempts to undo moves that succeeded. If a destination file has
    since been modified/moved away, that one file is reported as failed but
    the rest of the undo still proceeds.
    """
    folder = organize_result.folder
    undo_result = OrganizeResult(folder=folder)

    for outcome in organize_result.succeeded:
        src = os.path.join(folder, outcome.category, outcome.dest_filename)
        dest = os.path.join(folder, outcome.filename)

        try:
            if not os.path.isfile(src):
                raise FileNotFoundError(f"'{outcome.dest_filename}' is no longer where it was moved to.")
            if os.path.exists(dest):
                # Don't overwrite something that now exists at the original spot.
                dest = os.path.join(folder, _safe_destination_name(outcome.filename, set(os.listdir(folder))))

            shutil.move(src, dest)
            undo_result.outcomes.append(MoveOutcome(
                filename=outcome.dest_filename, category="(undo)",
                dest_filename=os.path.basename(dest), success=True,
            ))
        except OSError as e:
            undo_result.outcomes.append(MoveOutcome(
                filename=outcome.dest_filename, category="(undo)",
                dest_filename=outcome.filename, success=False, error=str(e),
            ))

    return undo_result
