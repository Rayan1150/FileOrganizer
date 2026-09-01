"""Thin Flask web UI for File Organizer.

This module only handles HTTP requests/rendering. All the real logic
(scanning, planning, moving files, undo) lives in app/organizer.py and has
no dependency on Flask, so it can be tested on its own.

This is a local, single-user tool: it keeps the current scan/last result in
plain module-level state rather than a database or session store, which is
enough for "one person, one browser tab, running it on their own machine."
"""
from __future__ import annotations

import secrets

from flask import Flask, redirect, render_template, request, url_for

from app.categorizer import all_category_names
from app.organizer import InvalidFolderError, ScanResult, organize, scan_folder, undo

# In-memory state for the current session. This app is meant to be run
# locally by one person at a time (see README) so this is intentionally
# simple rather than backed by a database.
_state: dict[str, object] = {
    "scan_result": None,       # ScanResult | None
    "organize_result": None,   # OrganizeResult | None
    "undo_result": None,       # OrganizeResult | None
    "error": None,             # str | None
}


def create_app() -> Flask:
    app = Flask(__name__)
    # Only used for flashing simple one-off messages between requests; a
    # random key is fine since nothing sensitive is stored in the session
    # and the app isn't meant to be exposed outside localhost.
    app.secret_key = secrets.token_hex(16)

    register_routes(app)
    return app


def register_routes(app: Flask) -> None:

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            scan_result=_state["scan_result"],
            organize_result=_state["organize_result"],
            undo_result=_state["undo_result"],
            error=_state["error"],
            category_names=all_category_names(),
        )

    @app.post("/scan")
    def scan():
        folder = request.form.get("folder", "")
        _state["organize_result"] = None
        _state["undo_result"] = None
        _state["error"] = None

        try:
            result = scan_folder(folder)
        except InvalidFolderError as e:
            _state["scan_result"] = None
            _state["error"] = str(e)
            return redirect(url_for("index"))

        _state["scan_result"] = result
        return redirect(url_for("index"))

    @app.post("/organize")
    def do_organize():
        scan_result: ScanResult | None = _state.get("scan_result")  # type: ignore[assignment]
        if scan_result is None or not scan_result.moves:
            _state["error"] = "No scan to organize. Please scan a folder first."
            return redirect(url_for("index"))

        result = organize(scan_result)
        _state["organize_result"] = result
        _state["undo_result"] = None
        _state["scan_result"] = None
        _state["error"] = None
        return redirect(url_for("index"))

    @app.post("/undo")
    def do_undo():
        organize_result = _state.get("organize_result")
        if organize_result is None:
            _state["error"] = "There is nothing to undo."
            return redirect(url_for("index"))

        undo_result = undo(organize_result)  # type: ignore[arg-type]
        _state["organize_result"] = None
        _state["undo_result"] = undo_result
        return redirect(url_for("index"))

    @app.post("/reset")
    def reset():
        _state["scan_result"] = None
        _state["organize_result"] = None
        _state["undo_result"] = None
        _state["error"] = None
        return redirect(url_for("index"))
