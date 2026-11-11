"""Single source of truth for navigation entries and their keyboard shortcuts."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NavItem:
    name: str
    key: str
    label: str


NAV: tuple[NavItem, ...] = (
    NavItem("dashboard", "1", "Dashboard"),
    NavItem("import", "2", "Import CSV"),
    NavItem("verify_all", "3", "Verify all students"),
    NavItem("verify_incomplete", "4", "Verify incomplete"),
    NavItem("retry_failed", "5", "Retry failed profiles"),
    NavItem("students", "6", "Student directory"),
    NavItem("inaccessible", "7", "Inaccessible profiles"),
    NavItem("failed", "8", "Failed records"),
    NavItem("review", "9", "Needs-review records"),
    NavItem("courses", "c", "Course-wise progress"),
    NavItem("export", "e", "Export reports"),
    NavItem("settings", "s", "Settings"),
)