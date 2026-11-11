from textual.widgets import Static


class StatCard(Static):
    """A labelled number. Variant (ok/warn/bad/info) only changes the accent colour."""

    def __init__(self, label: str, variant: str = "", *, id: str | None = None) -> None:
        super().__init__(id=id, classes=f"stat {variant}".strip())
        self._label = label
        self.set_value("0")

    def set_value(self, value: str | int) -> None:
        self.update(f"[b]{value}[/b]\n[dim]{self._label}[/dim]")