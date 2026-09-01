# File Organizer

A simple tool that organizes messy folders by sorting files into categories
like Documents, Images, Videos, Audio, Archives, and Applications.

Point it at a folder — your Downloads folder, for example — and it groups
the files inside by type, but only after showing you exactly what it's
going to do and waiting for you to confirm.

## Why I built it

My Downloads folder is a mess of PDFs, screenshots, installers, and random
archives all mixed together, and I kept meaning to clean it up "later." I
wanted a small tool that does the boring sorting part automatically, but
that I'd actually trust to run on real files — which meant it had to be
predictable, never delete anything, and never overwrite anything by
accident.

## Features

- Scans a folder (not its subfolders) and groups files into categories
  based on their file extension.
- Shows a full preview of every proposed move before anything happens.
- Only moves files after you click "Organize Files" — nothing happens on
  scan.
- Never overwrites an existing file. If `report.pdf` already exists in the
  destination, the new one is saved as `report (1).pdf` instead.
- Never deletes files, and never modifies file contents — files are only
  moved.
- Skips hidden files, symbolic links, and subfolders, so behavior stays
  predictable.
- Handles errors per-file: if one file can't be moved (permissions, or it
  disappeared), the rest of the folder is still organized, and you're told
  what failed.
- "Undo Last Organization" button to move everything back to where it was.
- Runs entirely on your own machine — nothing is uploaded anywhere.

## Built with

- Python 3
- Flask (a small local web server — the whole UI is just a couple of
  server-rendered pages, no JavaScript framework, no build step)
- Python's standard library for everything else (`os`, `shutil`)

No database, no accounts, no cloud storage, no AI. The "smart" part is a
plain dictionary mapping file extensions to category names — see
`app/categorizer.py`.

## Running it

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Then open **http://127.0.0.1:5050** in your browser, paste in a folder
path, and click Scan.

## Testing

```bash
python -m unittest discover -s tests -t . -v
```

This runs 42 tests covering categorization (common extensions, unknown
extensions, uppercase extensions, multi-dot filenames), the scanning and
safe-renaming logic, moving files (including collision handling, files that
disappear mid-run, and confirming nothing is ever deleted), the undo
feature, and every web route (including invalid/empty folders and the
full scan → organize → undo workflow). All tests run against temporary
directories created on the fly — never against a real folder on disk.

## Safety

This is the part I was most careful about:

- **Files are never deleted.** Organizing only moves files from the top
  level of a folder into subfolders inside that same folder.
- **Files are never overwritten.** If a name collision would happen, the
  incoming file is renamed with a `(1)`, `(2)`, ... suffix instead.
- **Nothing moves until you confirm.** Scanning only reads the folder and
  shows you a preview; moving only happens when you click "Organize
  Files."
- **Subfolders are left alone.** Only files directly inside the folder you
  pick are organized — nothing recursive, so it can't unexpectedly
  rearrange an entire directory tree.
- **One failure doesn't stop the rest.** If a single file can't be moved
  (permissions, or it was deleted by something else in the meantime), that
  one file is reported as failed and every other file is still organized.

## Known limitations

- "Undo" only remembers the most recent organize operation, and only for
  as long as the app keeps running (it's in-memory, not saved to disk) —
  this was a deliberate simplicity trade-off (see requirement to not
  complicate the project just to add Undo).
- Undo does not remove the category folders it created if they're now
  empty — it just moves the files back.
- This is a single-user, local tool: it's meant for one person running it
  on their own machine, not multiple people using it at once.

## Privacy

File Organizer runs entirely on your own machine. It does not upload
files anywhere, does not require an account, and does not include any
analytics or tracking.

## License

MIT — see [`LICENSE`](LICENSE).
