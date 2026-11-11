"""Native OS file dialog (Windows Explorer on Windows) via tkinter, which ships with Python."""

from __future__ import annotations


def pick_csv_file() -> str | None:
    """Return the chosen path, or None if cancelled. Raises RuntimeError if no dialog is available."""
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
    except Exception as exc:  # tkinter missing or no display
        raise RuntimeError("Native file dialog unavailable") from exc
    try:
        root.withdraw()
        root.attributes("-topmost", True)  # keep the dialog above the terminal
        path = filedialog.askopenfilename(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
    finally:
        root.destroy()
    return path or None