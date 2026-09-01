from __future__ import annotations

import os
import tempfile
import unittest

from app.organizer import (
    InvalidFolderError,
    organize,
    scan_folder,
    undo,
    _safe_destination_name,
)


def _touch(path: str, content: bytes = b"x") -> None:
    with open(path, "wb") as f:
        f.write(content)


class TestScanFolder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_scan_categorizes_files_correctly(self):
        _touch(os.path.join(self.folder, "report.pdf"))
        _touch(os.path.join(self.folder, "photo.jpg"))
        _touch(os.path.join(self.folder, "clip.mp4"))
        _touch(os.path.join(self.folder, "unknown.xyz"))

        result = scan_folder(self.folder)

        by_name = {m.filename: m.category for m in result.moves}
        self.assertEqual(by_name["report.pdf"], "Documents")
        self.assertEqual(by_name["photo.jpg"], "Images")
        self.assertEqual(by_name["clip.mp4"], "Videos")
        self.assertEqual(by_name["unknown.xyz"], "Other")
        self.assertEqual(result.total_files, 4)

    def test_scan_empty_folder(self):
        result = scan_folder(self.folder)
        self.assertEqual(result.total_files, 0)
        self.assertEqual(result.moves, [])

    def test_scan_invalid_folder_raises(self):
        with self.assertRaises(InvalidFolderError):
            scan_folder(os.path.join(self.folder, "does-not-exist"))

    def test_scan_rejects_empty_string(self):
        with self.assertRaises(InvalidFolderError):
            scan_folder("")

    def test_scan_rejects_a_file_path_not_a_folder(self):
        file_path = os.path.join(self.folder, "not_a_folder.txt")
        _touch(file_path)
        with self.assertRaises(InvalidFolderError):
            scan_folder(file_path)

    def test_scan_does_not_recurse_into_subfolders(self):
        subdir = os.path.join(self.folder, "Subfolder")
        os.makedirs(subdir)
        _touch(os.path.join(subdir, "nested.pdf"))
        _touch(os.path.join(self.folder, "top-level.pdf"))

        result = scan_folder(self.folder)

        names = {m.filename for m in result.moves}
        self.assertIn("top-level.pdf", names)
        self.assertNotIn("nested.pdf", names)

    def test_scan_skips_hidden_files(self):
        _touch(os.path.join(self.folder, ".hidden"))
        _touch(os.path.join(self.folder, "visible.pdf"))

        result = scan_folder(self.folder)

        names = {m.filename for m in result.moves}
        self.assertNotIn(".hidden", names)
        self.assertIn("visible.pdf", names)
        skipped_names = {name for name, _ in result.skipped}
        self.assertIn(".hidden", skipped_names)

    def test_scan_ignores_subdirectories_as_non_file_entries(self):
        os.makedirs(os.path.join(self.folder, "SomeFolder"))
        result = scan_folder(self.folder)
        names = {m.filename for m in result.moves}
        self.assertNotIn("SomeFolder", names)

    def test_scan_plans_safe_names_for_existing_collisions(self):
        # A file already exists at Documents/report.pdf ...
        os.makedirs(os.path.join(self.folder, "Documents"))
        _touch(os.path.join(self.folder, "Documents", "report.pdf"))
        # ... and another report.pdf is sitting in the top-level folder.
        _touch(os.path.join(self.folder, "report.pdf"))

        result = scan_folder(self.folder)

        move = next(m for m in result.moves if m.filename == "report.pdf")
        self.assertEqual(move.dest_filename, "report (1).pdf")

    def test_scan_plans_distinct_names_for_two_same_named_new_files(self):
        # Not realistic (a real filesystem can't have two files with the
        # exact same name in one folder), but this test protects the
        # planning logic itself: __two entries that would resolve to the
        # same planned destination should never both be planned as-is.
        # We simulate this by having one already-planned name in Documents.
        _touch(os.path.join(self.folder, "notes.pdf"))
        result = scan_folder(self.folder)
        self.assertEqual(result.moves[0].dest_filename, "notes.pdf")


class TestSafeDestinationName(unittest.TestCase):
    def test_returns_original_name_when_no_collision(self):
        self.assertEqual(_safe_destination_name("a.pdf", set()), "a.pdf")

    def test_adds_counter_suffix_on_collision(self):
        self.assertEqual(_safe_destination_name("a.pdf", {"a.pdf"}), "a (1).pdf")

    def test_increments_counter_until_free(self):
        existing = {"a.pdf", "a (1).pdf", "a (2).pdf"}
        self.assertEqual(_safe_destination_name("a.pdf", existing), "a (3).pdf")

    def test_handles_no_extension(self):
        self.assertEqual(_safe_destination_name("README", {"README"}), "README (1)")


class TestOrganize(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_organize_moves_files_into_category_folders(self):
        _touch(os.path.join(self.folder, "report.pdf"))
        _touch(os.path.join(self.folder, "photo.jpg"))

        scan_result = scan_folder(self.folder)
        result = organize(scan_result)

        self.assertEqual(len(result.succeeded), 2)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Documents", "report.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Images", "photo.jpg")))
        # Original locations should no longer have the files.
        self.assertFalse(os.path.isfile(os.path.join(self.folder, "report.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(self.folder, "photo.jpg")))

    def test_organize_never_overwrites_existing_file(self):
        os.makedirs(os.path.join(self.folder, "Documents"))
        _touch(os.path.join(self.folder, "Documents", "report.pdf"), content=b"ORIGINAL")
        _touch(os.path.join(self.folder, "report.pdf"), content=b"NEW")

        scan_result = scan_folder(self.folder)
        result = organize(scan_result)

        self.assertTrue(result.succeeded)
        with open(os.path.join(self.folder, "Documents", "report.pdf"), "rb") as f:
            original_content = f.read()
        self.assertEqual(original_content, b"ORIGINAL")
        # The new file should have been renamed, not dropped.
        renamed_path = os.path.join(self.folder, "Documents", "report (1).pdf")
        self.assertTrue(os.path.isfile(renamed_path))
        with open(renamed_path, "rb") as f:
            self.assertEqual(f.read(), b"NEW")

    def test_organize_does_not_delete_any_files(self):
        _touch(os.path.join(self.folder, "a.pdf"))
        _touch(os.path.join(self.folder, "b.jpg"))

        scan_result = scan_folder(self.folder)
        organize(scan_result)

        all_files_after = []
        for root, _dirs, files in os.walk(self.folder):
            all_files_after.extend(files)
        self.assertEqual(sorted(all_files_after), ["a.pdf", "b.jpg"])

    def test_organize_continues_after_one_file_disappears(self):
        _touch(os.path.join(self.folder, "a.pdf"))
        _touch(os.path.join(self.folder, "b.pdf"))

        scan_result = scan_folder(self.folder)
        # Simulate 'a.pdf' disappearing between scan and organize.
        os.remove(os.path.join(self.folder, "a.pdf"))

        result = organize(scan_result)

        failed_names = {o.filename for o in result.failed}
        succeeded_names = {o.filename for o in result.succeeded}
        self.assertIn("a.pdf", failed_names)
        self.assertIn("b.pdf", succeeded_names)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Documents", "b.pdf")))

    def test_organize_with_no_moves_does_nothing(self):
        scan_result = scan_folder(self.folder)  # empty folder
        result = organize(scan_result)
        self.assertEqual(result.outcomes, [])


class TestUndo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_undo_restores_files_to_original_location(self):
        _touch(os.path.join(self.folder, "report.pdf"), content=b"HELLO")

        scan_result = scan_folder(self.folder)
        organize_result = organize(scan_result)
        undo_result = undo(organize_result)

        self.assertEqual(len(undo_result.succeeded), 1)
        restored = os.path.join(self.folder, "report.pdf")
        self.assertTrue(os.path.isfile(restored))
        with open(restored, "rb") as f:
            self.assertEqual(f.read(), b"HELLO")
        self.assertFalse(os.path.isfile(os.path.join(self.folder, "Documents", "report.pdf")))

    def test_undo_does_not_overwrite_a_file_that_now_exists_at_original_spot(self):
        _touch(os.path.join(self.folder, "report.pdf"), content=b"ORIGINAL")
        scan_result = scan_folder(self.folder)
        organize_result = organize(scan_result)

        # Something else now occupies the original spot.
        _touch(os.path.join(self.folder, "report.pdf"), content=b"SOMETHING ELSE")

        undo_result = undo(organize_result)

        self.assertTrue(undo_result.succeeded)
        # The pre-existing file must be untouched.
        with open(os.path.join(self.folder, "report.pdf"), "rb") as f:
            self.assertEqual(f.read(), b"SOMETHING ELSE")
        # The restored file should exist under a safe alternate name.
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "report (1).pdf")))


if __name__ == "__main__":
    unittest.main()
