"""Progress tracking exposed as plain snapshots; the UI subscribes with a callback."""

from __future__ import annotations

import logging
from collections.abc import Callable

from core.constants import VerificationStatus as V
from services.verification_models import ProgressSnapshot, SelectionMode

logger = logging.getLogger(__name__)

_BUCKET = {
    V.COMPLETED: "completed", V.INCOMPLETE: "incomplete", V.FAILED: "failed",
    V.PRIVATE_INACCESSIBLE: "inaccessible", V.NOT_FOUND: "not_found", V.NEEDS_REVIEW: "needs_review",
}


class ProgressTracker:
    def __init__(self, mode: SelectionMode, total: int,
                 listener: Callable[[ProgressSnapshot], None] | None = None) -> None:
        self._mode, self._total, self._listener = mode, total, listener
        self._counts = dict.fromkeys(_BUCKET.values(), 0)
        self._state, self._error = "idle", None

    def snapshot(self) -> ProgressSnapshot:
        processed = sum(self._counts.values())
        return ProgressSnapshot(mode=self._mode, state=self._state, total_selected=self._total,
                                processed=processed, remaining=self._total - processed,
                                error=self._error, **self._counts)

    def _emit(self) -> None:
        if self._listener is None:
            return
        try:
            self._listener(self.snapshot())
        except Exception:  # a broken UI callback must never break verification
            logger.exception("Progress listener failed")

    def start(self) -> None:
        self._state = "running"
        self._emit()

    def record(self, status: V) -> None:
        self._counts[_BUCKET[status]] += 1
        self._emit()

    def finish(self, state: str, error: str | None = None) -> ProgressSnapshot:
        self._state, self._error = state, error
        self._emit()
        return self.snapshot()