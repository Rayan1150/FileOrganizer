"""Regression tests for issues found during an adversarial safety audit.

Each test documents the real-world scenario that motivated it.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from unittest import mock

from app.organizer import (
    InvalidFolderError,
    _category_dir_problem,
    _has_unsupported_characters,
    _safe_destination_name,
    organize,
    scan_folder,
    undo,
)


def _can_create_symlinks() -> bool:
    """Whether this process can actually create a symlink right now.

    On Windows, creating a symlink needs either Administrator rights or
    Developer Mode enabled (SeCreateSymbolicLinkPrivilege) - without it,
    os.symlink() raises OSError (WinError 1314). This probes for that
    capability once, instead of assuming the OS based on sys.platform alone,
    since the real constraint is the privilege, not the platform name.
    """
    with tempfile.TemporaryDirectory() as probe_dir:
        link_path = os.path.join(probe_dir, "link")
        try:
            os.symlink(os.path.join(probe_dir, "target-does-not-need-to-exist"), link_path)
        except OSError:
            return False
        return True


_SYMLINKS_SUPPORTED = _can_create_symlinks()


def _touch(path: str, content: bytes = b"x") -> None:
    with open(path, "wb") as f:
        f.write(content)


class TestSafetyFixes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    # -- collisions are compared without regard to upper/lower case ----------
    # Windows and macOS treat 'REPORT.PDF' and 'report.pdf' as the same file;
    # planning them as different names could make one silently replace the other.

    def test_destination_names_collide_case_insensitively(self):
        self.assertEqual(
            _safe_destination_name("REPORT.PDF", {"report.pdf"}), "REPORT (1).PDF"
        )

    def test_scan_handles_mixed_case_collision(self):
        os.makedirs(os.path.join(self.folder, "Documents"))
        _touch(os.path.join(self.folder, "Documents", "report.pdf"))
        _touch(os.path.join(self.folder, "REPORT.PDF"))

        result = scan_folder(self.folder)
        move = result.moves[0]
        self.assertNotEqual(move.dest_filename.lower(), "report.pdf")

    # -- the app refuses to organize its own project folder ------------------

    def test_refuses_to_scan_its_own_project_folder(self):
        app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        with self.assertRaises(InvalidFolderError):
            scan_folder(app_root)

    # -- a category "folder" that's actually a regular file -------------------

    def test_file_blocking_category_folder_is_reported_not_crashed(self):
        _touch(os.path.join(self.folder, "Documents"))  # a FILE (no extension) named "Documents"
        _touch(os.path.join(self.folder, "a.pdf"))

        scan_result = scan_folder(self.folder)
        # a.pdf can't be planned into a blocked "Documents" category, so it's
        # skipped rather than silently dropped or crashing the scan.
        moved_names = {m.filename for m in scan_result.moves}
        self.assertNotIn("a.pdf", moved_names)
        skipped_names = {name: reason for name, reason in scan_result.skipped}
        self.assertIn("a.pdf", skipped_names)
        self.assertIn("Documents", skipped_names["a.pdf"])

    # -- a category "folder" that's a symlink pointing elsewhere --------------
    #
    # Two tests cover this: one exercises the real production safety check
    # (_category_dir_problem) directly against a path reported as a symlink,
    # which needs no OS privilege and so always runs. The other does the full,
    # real thing with an actual symlink and the real organize()/scan_folder()
    # pipeline, which is the strongest evidence but needs a capability
    # (Developer Mode or admin on Windows) that isn't always available - it's
    # skipped, not weakened, when that capability genuinely isn't there.

    def test_category_dir_problem_flags_a_symlinked_folder(self):
        category_dir = os.path.join(self.folder, "Documents")
        os.makedirs(category_dir)  # a real directory; only *reported* as a symlink below

        with mock.patch("app.organizer.os.path.islink", side_effect=lambda p: p == category_dir):
            problem = _category_dir_problem(category_dir, "Documents")

        self.assertIsNotNone(problem)
        self.assertIn("shortcut", problem)

    @unittest.skipUnless(
        _SYMLINKS_SUPPORTED,
        "this OS/user account can't create symlinks here (e.g. Windows without "
        "Developer Mode or Administrator rights) - see _can_create_symlinks()",
    )
    def test_symlinked_category_folder_is_not_used_end_to_end(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(outside) if os.path.isdir(outside) and not os.listdir(outside) else None)
        os.symlink(outside, os.path.join(self.folder, "Documents"))
        _touch(os.path.join(self.folder, "a.pdf"))

        result = organize(scan_folder(self.folder))

        self.assertFalse(os.path.isfile(os.path.join(outside, "a.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "a.pdf")))  # left in place

    # -- in-progress downloads are left alone ---------------------------------

    def test_unfinished_download_is_skipped(self):
        _touch(os.path.join(self.folder, "movie.mp4.crdownload"))
        result = scan_folder(self.folder)
        self.assertEqual(result.moves, [])
        self.assertEqual(result.skipped[0][0], "movie.mp4.crdownload")

    # -- quoted paths (Windows "Copy as path") are accepted -------------------

    def test_quoted_path_is_accepted(self):
        # Windows' "Copy as path" wraps the path in double quotes.
        result = scan_folder(f'"{self.folder}"')
        self.assertTrue(os.path.isdir(result.folder))
        self.assertEqual(os.path.normcase(result.folder), os.path.normcase(os.path.abspath(self.folder)))

    # -- undo cleans up the category folder it created, if now empty ---------

    def test_undo_removes_category_folder_it_created(self):
        _touch(os.path.join(self.folder, "a.pdf"))
        organize_result = organize(scan_folder(self.folder))
        undo(organize_result)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "Documents")))

    def test_undo_keeps_category_folder_that_already_existed(self):
        os.makedirs(os.path.join(self.folder, "Documents"))
        _touch(os.path.join(self.folder, "a.pdf"))
        organize_result = organize(scan_folder(self.folder))
        undo(organize_result)
        # The folder existed before organize() ran, so undo() must not remove it.
        self.assertTrue(os.path.isdir(os.path.join(self.folder, "Documents")))

    def test_undo_keeps_category_folder_that_still_has_other_files(self):
        _touch(os.path.join(self.folder, "a.pdf"))
        _touch(os.path.join(self.folder, "b.pdf"))
        organize_result = organize(scan_folder(self.folder))
        # Pretend only a.pdf's move is being undone (simulating a second,
        # still-organized file left behind in the same category).
        partial = type(organize_result)(folder=organize_result.folder,
                                         outcomes=[organize_result.outcomes[0]],
                                         created_folders=organize_result.created_folders)
        undo(partial)
        self.assertTrue(os.path.isdir(os.path.join(self.folder, "Documents")))

    # -- undo refuses to move back a file that was changed since organizing --

    def test_undo_refuses_a_file_that_changed_after_organizing(self):
        _touch(os.path.join(self.folder, "a.pdf"), content=b"original")
        organize_result = organize(scan_folder(self.folder))
        moved_path = os.path.join(self.folder, "Documents", "a.pdf")
        # Someone/something edits the file after it was organized.
        with open(moved_path, "ab") as f:
            f.write(b"-edited")

        undo_result = undo(organize_result)

        self.assertEqual(len(undo_result.failed), 1)
        # The edited file must still be sitting in Documents/, untouched otherwise.
        self.assertTrue(os.path.isfile(moved_path))
        with open(moved_path, "rb") as f:
            self.assertEqual(f.read(), b"original-edited")

    # -- a filename with surrogate/invalid-encoding bytes doesn't crash the app
    #
    # Two tests cover this, for the same reason as the symlink tests above.
    # The real-world scenario is: on a POSIX file system, os.scandir() can
    # return a filename that isn't valid Unicode (e.g. it was created with a
    # non-UTF-8 locale), which Python represents as a string containing a
    # "lone surrogate" character standing in for the raw byte. Windows
    # filenames are always valid Unicode, so this exact situation can't be
    # created there - proven by the UnicodeDecodeError raised by Windows'
    # own os.open() when the earlier version of this test tried to force it.

    def test_has_unsupported_characters_detects_a_lone_surrogate(self):
        # '\udcff' is exactly what Python's surrogateescape error handler
        # produces for a raw 0xFF byte that isn't valid UTF-8 - this is the
        # real condition _reason_to_skip() is guarding against, built directly
        # as a string so the test needs no OS/filesystem cooperation at all.
        self.assertTrue(_has_unsupported_characters("bad\udcffname.pdf"))

    def test_has_unsupported_characters_accepts_normal_names(self):
        self.assertFalse(_has_unsupported_characters("vacation photo.jpg"))
        self.assertFalse(_has_unsupported_characters("café notes.txt"))  # ordinary non-ASCII is fine

    def test_filename_with_invalid_encoding_is_skipped_not_crashed_end_to_end(self):
        try:
            raw_path = os.fsencode(self.folder) + b"/bad\xffname.pdf"
            fd = os.open(raw_path, os.O_CREAT | os.O_WRONLY)
            os.close(fd)
        except (OSError, UnicodeDecodeError, UnicodeError, ValueError) as error:
            # This file system/OS genuinely can't represent the byte sequence
            # as a filename (confirmed on Windows: os.open() itself raises
            # UnicodeDecodeError trying to round-trip the bytes through its
            # wide-char API) - the pure-logic tests above still cover the
            # actual safety behavior unconditionally.
            self.skipTest(f"this OS/file system can't create this filename ({error})")

        result = scan_folder(self.folder)  # must not raise
        self.assertEqual(result.moves, [])
        self.assertEqual(len(result.skipped), 1)


if __name__ == "__main__":
    unittest.main()
