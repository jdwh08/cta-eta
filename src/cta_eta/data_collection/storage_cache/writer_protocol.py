"""Common protocol types for storage cache writers."""

from __future__ import annotations

from typing import Any, Protocol


class DataWriter(Protocol):
    """Protocol for data writers that accept batched records."""

    def append_batch(
        self,
        records: list[dict[str, Any]],
        dataset_name: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a batch of records."""
        ...

    def close(self) -> None:
        """Close any open resources."""
        ...


class RotatableWriter(DataWriter, Protocol):
    """Protocol for writers with explicit rotation support."""

    def rotate(self) -> None:
        """Rotate output to a new file/segment."""
        ...
