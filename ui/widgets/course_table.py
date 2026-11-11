from textual.widgets import DataTable

from services.reports import CourseCompletion
from ui.formatting import bar, fmt_pct


class CourseProgressTable(DataTable):
    """Per-course completion. Percentages use only successfully evaluated students."""

    def __init__(self, **kw) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kw)

    def on_mount(self) -> None:
        self.add_columns("Course", "Completed", "Missing", "%", "Progress")

    def show(self, courses: list[CourseCompletion]) -> None:
        self.clear()
        for c in courses:
            self.add_row(c.course_name, str(c.completed_students), str(c.evaluated_students - c.completed_students),
                         fmt_pct(c.percent), bar(c.percent), key=c.template_id)