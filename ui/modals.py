"""Reusable modal dialogs."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Label, Static


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "answer(False)", "Cancel"), Binding("y", "answer(True)", "Yes")]

    def __init__(self, title: str, body: str, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self._title, self._body, self._label = title, body, confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._body)
            with Horizontal(classes="dialog-buttons"):
                yield Button(self._label, variant="success", id="yes")
                yield Button("Cancel (Esc)", id="no")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class DetailModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title, self._body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(self._title, classes="dialog-title")
            with VerticalScroll():
                yield Static(self._body, markup=False, id="detail-body")
            yield Button("Close (Esc)", id="close")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss(None)


class _CsvTree(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if p.is_dir() and not p.name.startswith(".") or p.suffix.lower() == ".csv"]


class FilePickerModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, start: Path | None = None) -> None:
        super().__init__()
        self._start = start or Path.cwd()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Select a CSV file (Enter to choose)", classes="dialog-title")
            yield _CsvTree(self._start, id="picker")
            yield Button("Cancel (Esc)", id="cancel")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(str(event.path))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss(None)