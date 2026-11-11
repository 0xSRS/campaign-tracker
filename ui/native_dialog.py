"""Native file dialog (Windows Explorer) run in a separate process, so it can't clash with the terminal UI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = r'''
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askopenfilename(
    title="Select CSV file",
    initialdir=sys.argv[1],
    filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
)
root.destroy()
sys.stdout.write(path)
'''


def pick_csv_file() -> str | None:
    """Chosen path, or None if cancelled. Raises RuntimeError with the reason if the dialog can't open."""
    downloads = Path.home() / "Downloads"
    start = downloads if downloads.is_dir() else Path.home()
    try:
        done = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(start)],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except Exception as exc:
        raise RuntimeError(f"Could not open the file dialog: {exc}") from exc
    if done.returncode != 0:
        lines = done.stderr.strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "File dialog failed")
    return done.stdout.strip() or None