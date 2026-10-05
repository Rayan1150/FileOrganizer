"""Run File Organizer locally.

Usage:
    python run.py

Then open http://127.0.0.1:5050 in your browser.
"""
from __future__ import annotations

import sys

from app.web import create_app

HOST = "127.0.0.1"  # local machine only - never exposed on the network
PORT = 5050

if __name__ == "__main__":
    app = create_app()
    try:
        print(f"File Organizer running at http://{HOST}:{PORT}")
        app.run(host=HOST, port=PORT, debug=False)
    except OSError as error:
        if "Address already in use" in str(error) or getattr(error, "errno", None) == 98:
            print(f"Port {PORT} is already in use. Close whatever is using it, "
                  f"or edit PORT in run.py, then try again.")
            sys.exit(1)
        raise
