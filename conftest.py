"""Pytest bootstrap: isolate test data under a temp dir BEFORE app/paths import,
so tests never touch the real %LOCALAPPDATA%\\FasterNotes data. Living at the repo
root also puts app.py / paths.py on sys.path for `import app`."""
import os
import tempfile

os.environ.setdefault("FASTER_NOTES_DATA", tempfile.mkdtemp(prefix="fn_test_"))
