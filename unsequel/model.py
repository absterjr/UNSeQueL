from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Table:
    """A tiny in-memory relation: ordered columns plus dictionary rows."""

    name: str
    columns: list[str]
    rows: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)
