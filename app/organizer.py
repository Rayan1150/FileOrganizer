"""Core File Organizer logic: scanning a folder, building a move plan,
executing it safely, and undoing the most recent operation.

This module has no dependency on Flask or any UI - it's plain Python so it
can be tested and reasoned about on its own.

The safety rules it enforces:
  * only files directly inside the chosen folder are touched (no recursion)
  * nothing is ever deleted or overwritten - name clashes get "(1)", "(2)", ...
  * every move stays inside the chosen folder
  * one failing file never stops the others
"""
from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field

from app.categorizer import category_for_filename

# Visible files we never touch and don't bother mentioning (compared in lower case).
IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}

# A browser (or another program) is probably still writing to these files, so
# moving them could break a download that is in progress.
UNFINISHED_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp")

# Undo compares a file's size and modified time with what we recorded when we
# moved it. Some file systems (e.g. FAT) only store times to the nearest 2 seconds.
MTIME_TOLERANCE_NS = 2_000_000_000

# The folder that contains this project. Organizing it would move run.py etc.
_APP_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))


class InvalidFolderError(Exception):
    """Raised when the given path is not a usable, accessible directory."""


class _CannotMove(Exception):
    """A move was refused for a reason we can explain in plain words."""


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
    # What the file looked like when we moved it, so Undo can tell whether it is
    # still the same file. Only set for successful moves.
    size: int | None = None
    mtime_ns: int | None = None


@dataclass
class OrganizeResult:
    folder: str
    outcomes: list[MoveOutcome] = field(default_factory=list)
    # Category folders that did not exist before and were created by this run.
    created_folders: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> list[MoveOutcome]:
        return [o for o in self.outcomes if o.success]

    @property
    def failed(self) -> list[MoveOutcome]:
        return [o for o in self.outcomes if not o.success]


# --------------------------------------------------------------------------
# Folder checks
# --------------------------------------------------------------------------

def _is_same_or_inside(path: str, parent: str) -> bool:
    path, parent = os.path.normcase(path), os.path.normcase(parent)
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def _system_folders() -> list[str]:
    """Operating-system folders that must never be reorganized."""
    if os.name == "nt":
        variables = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
        return [os.environ[name] for name in variables if os.environ.get(name)]
    return ["/bin", "/sbin", "/usr", "/etc", "/lib", "/lib32", "/lib64",
            "/boot", "/dev", "/proc", "/sys", "/System", "/Library"]


def _protected_reason(path: str) -> str | None:
    """Why this folder is too risky to organize, or None if it is fine."""
    real = os.path.realpath(path)
    if os.path.dirname(real) == real:
        return "the top level of a drive"
    if _is_same_or_inside(real, _APP_ROOT):
        return "File Organizer's own project folder"
    for system_folder in _system_folders():
        if _is_same_or_inside(real, os.path.realpath(system_folder)):
            return "a system folder"
    return None


def _strip_matching_quotes(text: str) -> str:
    # Windows' "Copy as path" wraps the path in double quotes.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


def _validate_folder(folder: str) -> str:
    """Return a normalized absolute path, or raise InvalidFolderError."""
    cleaned = _strip_matching_quotes((folder or "").strip())
    if not cleaned:
        raise InvalidFolderError("No folder was selected.")

    path = os.path.abspath(os.path.expanduser(cleaned))

    if not os.path.exists(path):
        raise InvalidFolderError(f"This folder does not exist: {path}")
    if not os.path.isdir(path):
        raise InvalidFolderError(f"This is not a folder: {path}")

    reason = _protected_reason(path)
    if reason:
        raise InvalidFolderError(f"For safety, File Organizer won't organize {reason}: {path}")

    if not os.access(path, os.R_OK | os.W_OK):
        raise InvalidFolderError(f"This folder could not be accessed: {path}")

    return path


def _is_junction(path: str) -> bool:
    is_junction = getattr(os.path, "isjunction", None)  # Python 3.12+
    return bool(is_junction and is_junction(path))


def _category_dir_problem(category_dir: str, category: str) -> str | None:
    """Why files can't safely be moved into this category folder, or None if fine."""
    if os.path.islink(category_dir) or _is_junction(category_dir):
        return (f"the '{category}' folder is a shortcut (symbolic link), so files "
                "moved into it could end up outside this folder")
    if os.path.exists(category_dir) and not os.path.isdir(category_dir):
        return f"a file named '{category}' is in the way of the '{category}' folder"
    return None


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    # Windows marks hidden/system files with attributes instead of a leading dot.
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


def _has_unsupported_characters(name: str) -> bool:
    """True if `name` contains characters this app can't safely carry through
    (e.g. lone surrogate code points). This can happen with os.scandir() on a
    POSIX file system when a filename isn't valid UTF-8 to begin with - Python
    decodes it using surrogate characters that stand for the raw bytes, and
    those can't safely round-trip back through a normal string operation.

    This is a pure string check (no filesystem access) specifically so it can
    be tested directly with a crafted string, on any operating system -
    actually creating such a filename on disk isn't possible everywhere
    (Windows filenames are always valid Unicode, so this situation is a
    POSIX-specific edge case; see tests/test_organizer_safety.py).
    """
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def _reason_to_skip(entry: os.DirEntry) -> str | None:
    """Why a regular file should be left alone, or None if it can be organized."""
    if _is_hidden(entry):
        return "hidden or system file"
    if entry.name.lower().endswith(UNFINISHED_SUFFIXES):
        return "looks like an unfinished download or temporary file"
    if _has_unsupported_characters(entry.name):
        return "the file name has characters this app can't handle"
    return None


def _existing_names(category_dir: str) -> set[str]:
    if not os.path.isdir(category_dir):
        return set()
    try:
        return set(os.listdir(category_dir))
    except OSError:
        return set()


def _split_name(filename: str) -> tuple[str, str]:
    """Split 'a.txt' into ('a', '.txt'); keeps 'a.tar.gz' together as ('a', '.tar.gz')."""
    base, extension = os.path.splitext(filename)
    if base.lower().endswith(".tar"):
        base, extension = base[:-4], base[-4:] + extension
    return base, extension


def _safe_destination_name(filename: str, existing_names) -> str:
    """Return `filename`, or 'name (1).ext', 'name (2).ext', ... if it would clash
    with a name in `existing_names`.

    Names are compared ignoring upper/lower case: on Windows and macOS,
    'Report.PDF' and 'report.pdf' are the same file, so treating them as
    different could overwrite one with the other.
    """
    taken = {name.casefold() for name in existing_names}
    if filename.casefold() not in taken:
        return filename

    base, extension = _split_name(filename)
    counter = 1
    while f"{base} ({counter}){extension}".casefold() in taken:
        counter += 1
    return f"{base} ({counter}){extension}"


def scan_folder(folder: str) -> ScanResult:
    """Scan `folder` (non-recursively) and build a plan of proposed moves.

    Only reads the folder: no files are moved, created or changed. It also
    checks which destination names are already taken, so the plan never
    contains a name clash.
    """
    path = _validate_folder(folder)
    result = ScanResult(folder=path)

    try:
        with os.scandir(path) as listing:
            entries = sorted(listing, key=lambda entry: (entry.name.casefold(), entry.name))
    except OSError as error:
        raise InvalidFolderError(f"This folder could not be read: {_friendly_error(error)}") from error

    # Names already used (on disk, or planned earlier in this scan) per category.
    taken_names: dict[str, set[str]] = {}
    category_problems: dict[str, str | None] = {}

    for entry in entries:
        name = entry.name
        if name.casefold() in IGNORED_NAMES:
            continue

        try:
            if entry.is_symlink():
                result.skipped.append((name, "shortcut (symbolic link), left alone"))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue  # subfolders (and other non-files) are never touched
            reason = _reason_to_skip(entry)
        except OSError:
            result.skipped.append((name, "could not be inspected"))
            continue

        if reason:
            result.skipped.append((name, reason))
            continue

        category = category_for_filename(name)
        category_dir = os.path.join(path, category)
        if category not in category_problems:
            category_problems[category] = _category_dir_problem(category_dir, category)
        if category_problems[category]:
            result.skipped.append((name, category_problems[category]))
            continue

        names = taken_names.setdefault(category, _existing_names(category_dir))
        dest_name = _safe_destination_name(name, names)
        names.add(dest_name)
        result.moves.append(PlannedMove(filename=name, category=category, dest_filename=dest_name))

    return result


# --------------------------------------------------------------------------
# Organizing and undoing
# --------------------------------------------------------------------------

def _friendly_error(error: Exception) -> str:
    """Turn an exception into a sentence a non-programmer can understand."""
    if isinstance(error, _CannotMove):
        return str(error)
    if isinstance(error, PermissionError):
        return "Permission denied. The file may be open in another program, or the folder may be read-only."
    if isinstance(error, FileNotFoundError):
        return "The file is no longer where it was."
    if isinstance(error, FileExistsError):
        return "Something with the same name already exists."
    reason = getattr(error, "strerror", None) or str(error) or type(error).__name__
    return f"The file could not be moved ({reason})."


def _free_name_in(directory: str, filename: str) -> str:
    """A version of `filename` that doesn't clash with anything in `directory`."""
    return _safe_destination_name(filename, os.listdir(directory))


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

        try:
            # lstat (not stat): a shortcut that replaced the file since the scan is not a file.
            src_stat = os.lstat(src)
            if not stat.S_ISREG(src_stat.st_mode):
                raise FileNotFoundError(src)

            problem = _category_dir_problem(category_dir, move.category)
            if problem:
                raise _CannotMove(f"Not moved because {problem}.")

            folder_existed = os.path.isdir(category_dir)
            os.makedirs(category_dir, exist_ok=True)
            if not folder_existed and move.category not in result.created_folders:
                result.created_folders.append(move.category)

            # The plan was made earlier; if that name has been taken since, pick
            # a new one rather than overwrite anything.
            dest_name = move.dest_filename
            if os.path.lexists(os.path.join(category_dir, dest_name)):
                dest_name = _free_name_in(category_dir, dest_name)

            shutil.move(src, os.path.join(category_dir, dest_name))
            result.outcomes.append(MoveOutcome(
                filename=move.filename, category=move.category, dest_filename=dest_name,
                success=True, size=src_stat.st_size, mtime_ns=src_stat.st_mtime_ns,
            ))
        except (OSError, _CannotMove) as error:
            result.outcomes.append(MoveOutcome(
                filename=move.filename, category=move.category, dest_filename=move.dest_filename,
                success=False, error=_friendly_error(error),
            ))

    return result


def _looks_unchanged(path: str, outcome: MoveOutcome) -> bool:
    """True if the file at `path` still matches what we recorded when we moved it."""
    if outcome.size is None or outcome.mtime_ns is None:
        return True
    current = os.lstat(path)
    return (stat.S_ISREG(current.st_mode)
            and current.st_size == outcome.size
            and abs(current.st_mtime_ns - outcome.mtime_ns) <= MTIME_TOLERANCE_NS)


def undo(organize_result: OrganizeResult) -> OrganizeResult:
    """Move every successfully-moved file back to its original location.

    Only files that were really moved are considered, and each is checked to
    still be the same file before it is touched. Anything that has changed or
    disappeared is left where it is and reported; the rest are still restored.
    Category folders that organize() created are removed if they are now empty.
    """
    folder = organize_result.folder
    undo_result = OrganizeResult(folder=folder)

    for outcome in organize_result.succeeded:
        src = os.path.join(folder, outcome.category, outcome.dest_filename)
        try:
            if not os.path.lexists(src):
                raise FileNotFoundError(src)
            if not _looks_unchanged(src, outcome):
                raise _CannotMove("This file was changed after it was organized, so it was left where it is.")

            dest_name = outcome.filename
            if os.path.lexists(os.path.join(folder, dest_name)):
                # Something is at the original spot now; never overwrite it.
                dest_name = _free_name_in(folder, dest_name)

            shutil.move(src, os.path.join(folder, dest_name))
            undo_result.outcomes.append(MoveOutcome(
                filename=outcome.dest_filename, category="(undo)", dest_filename=dest_name, success=True,
            ))
        except (OSError, _CannotMove) as error:
            undo_result.outcomes.append(MoveOutcome(
                filename=outcome.dest_filename, category="(undo)", dest_filename=outcome.filename,
                success=False, error=_friendly_error(error),
            ))

    for category in organize_result.created_folders:
        try:
            os.rmdir(os.path.join(folder, category))  # refuses to remove a folder that has anything in it
        except OSError:
            pass

    return undo_result
