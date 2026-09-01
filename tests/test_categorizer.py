from __future__ import annotations

import unittest

from app.categorizer import all_category_names, category_for_filename


class TestCategorizer(unittest.TestCase):
    def test_documents(self):
        self.assertEqual(category_for_filename("report.pdf"), "Documents")
        self.assertEqual(category_for_filename("notes.docx"), "Documents")

    def test_images(self):
        self.assertEqual(category_for_filename("photo.jpg"), "Images")
        self.assertEqual(category_for_filename("icon.svg"), "Images")

    def test_videos(self):
        self.assertEqual(category_for_filename("clip.mp4"), "Videos")

    def test_audio(self):
        self.assertEqual(category_for_filename("song.mp3"), "Audio")

    def test_archives(self):
        self.assertEqual(category_for_filename("backup.zip"), "Archives")

    def test_applications(self):
        self.assertEqual(category_for_filename("setup.exe"), "Applications")

    def test_unknown_extension_goes_to_other(self):
        self.assertEqual(category_for_filename("data.xyz"), "Other")

    def test_no_extension_goes_to_other(self):
        self.assertEqual(category_for_filename("README"), "Other")

    def test_uppercase_extension_is_matched(self):
        self.assertEqual(category_for_filename("PHOTO.JPG"), "Images")
        self.assertEqual(category_for_filename("Report.PDF"), "Documents")

    def test_multiple_dots_uses_last_extension(self):
        self.assertEqual(category_for_filename("archive.tar.gz"), "Archives")
        self.assertEqual(category_for_filename("my.notes.final.docx"), "Documents")

    def test_filename_with_spaces(self):
        self.assertEqual(category_for_filename("vacation photo.jpg"), "Images")

    def test_all_category_names_includes_other_last(self):
        names = all_category_names()
        self.assertIn("Documents", names)
        self.assertEqual(names[-1], "Other")


if __name__ == "__main__":
    unittest.main()
