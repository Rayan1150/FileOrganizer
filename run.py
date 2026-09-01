"""Run File Organizer locally.

Usage:
    python run.py

Then open http://127.0.0.1:5050 in your browser.
"""
from __future__ import annotations

from app.web import create_app

if __name__ == "__main__":
    app = create_app()
    print("File Organizer running at http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, debug=False)
