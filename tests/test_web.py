from __future__ import annotations

import os
import tempfile
import unittest

from app.web import create_app, _state


def _touch(path: str) -> None:
    with open(path, "wb") as f:
        f.write(b"x")


class TestWebRoutes(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()
        # Web state is module-level (see app/web.py) — reset it between
        # tests so they don't leak into each other.
        _state["scan_result"] = None
        _state["organize_result"] = None
        _state["undo_result"] = None
        _state["error"] = None

        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_index_loads(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"File Organizer", resp.data)

    def test_scan_with_empty_folder_shows_error(self):
        resp = self.client.post("/scan", data={"folder": ""}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"No folder was selected", resp.data)

    def test_scan_with_invalid_folder_shows_error(self):
        resp = self.client.post(
            "/scan", data={"folder": "/definitely/not/a/real/path/xyz"}, follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"does not exist", resp.data)

    def test_scan_with_valid_folder_shows_preview(self):
        _touch(os.path.join(self.folder, "report.pdf"))
        _touch(os.path.join(self.folder, "photo.jpg"))

        resp = self.client.post("/scan", data={"folder": self.folder}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"2 files found", resp.data)
        self.assertIn(b"report.pdf", resp.data)
        self.assertIn(b"Organize Files", resp.data)

    def test_organize_without_prior_scan_shows_error(self):
        resp = self.client.post("/organize", follow_redirects=True)
        self.assertIn(b"scan a folder first", resp.data)

    def test_full_workflow_scan_then_organize(self):
        _touch(os.path.join(self.folder, "report.pdf"))
        _touch(os.path.join(self.folder, "photo.jpg"))

        self.client.post("/scan", data={"folder": self.folder}, follow_redirects=True)

        # Nothing should have moved yet — only after /organize is called.
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "report.pdf")))

        resp = self.client.post("/organize", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"organized", resp.data)

        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Documents", "report.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Images", "photo.jpg")))
        self.assertFalse(os.path.isfile(os.path.join(self.folder, "report.pdf")))

    def test_undo_after_organize_restores_files(self):
        _touch(os.path.join(self.folder, "report.pdf"))
        self.client.post("/scan", data={"folder": self.folder}, follow_redirects=True)
        self.client.post("/organize", follow_redirects=True)

        resp = self.client.post("/undo", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Undo complete", resp.data)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "report.pdf")))

    def test_failed_scan_does_not_erase_pending_undo(self):
        # A typo'd/invalid folder afterwards must not cost the user their
        # ability to undo a just-completed organize.
        _touch(os.path.join(self.folder, "a.pdf"))
        self.client.post("/scan", data={"folder": self.folder}, follow_redirects=True)
        self.client.post("/organize", follow_redirects=True)

        resp = self.client.post(
            "/scan", data={"folder": "/definitely/not/a/real/path/xyz"}, follow_redirects=True
        )
        self.assertIn(b"does not exist", resp.data)
        self.assertIn(b"Undo Last Organization", resp.data)

        undo_resp = self.client.post("/undo", follow_redirects=True)
        self.assertIn(b"Undo complete", undo_resp.data)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "a.pdf")))

    def test_undo_without_prior_organize_shows_error(self):
        resp = self.client.post("/undo", follow_redirects=True)
        self.assertIn(b"nothing to undo", resp.data)

    def test_double_submit_of_organize_does_not_race(self):
        # Simulates an accidental double-click: the second /organize call
        # should find nothing left to organize, not race the first call
        # over the same files.
        _touch(os.path.join(self.folder, "a.pdf"))
        self.client.post("/scan", data={"folder": self.folder}, follow_redirects=True)

        first = self.client.post("/organize", follow_redirects=True)
        second = self.client.post("/organize", follow_redirects=True)

        self.assertIn(b"organized", first.data)
        self.assertIn(b"No scan to organize", second.data)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Documents", "a.pdf")))

    def test_responses_include_basic_security_headers(self):
        resp = self.client.get("/")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")

    def test_reset_clears_state(self):
        _touch(os.path.join(self.folder, "report.pdf"))
        self.client.post("/scan", data={"folder": self.folder}, follow_redirects=True)
        self.client.post("/reset", follow_redirects=True)

        resp = self.client.get("/")
        self.assertIn(b"Scan", resp.data)
        self.assertNotIn(b"Organize Files", resp.data)


if __name__ == "__main__":
    unittest.main()
