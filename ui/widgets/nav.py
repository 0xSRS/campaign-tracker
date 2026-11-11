from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ui.navigation import NAV


class NavSidebar(OptionList):
    """Keyboard-navigable menu; each entry shows its global shortcut."""

    def __init__(self, current: str) -> None:
        super().__init__(*[Option(f"{i.key:>2}  {i.label}", id=i.name) for i in NAV], id="nav")
        self._current = current

    def on_mount(self) -> None:
        names = [i.name for i in NAV]
        if self._current in names:
            self.highlighted = names.index(self._current)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.app.goto(event.option_id)