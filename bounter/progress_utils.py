"""Helpers for coordinating Rich progress displays."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from rich.progress import Progress


@contextmanager
def track_progress(progress: Optional[Progress], description: str) -> Iterator[None]:
    """Create a scoped progress task when a Progress instance is available."""

    if progress is None:
        yield
        return

    task_id = progress.add_task(description, total=None)
    try:
        yield
    finally:
        progress.remove_task(task_id)
