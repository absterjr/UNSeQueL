class UnsequelError(Exception):
    """Base class for user-facing UNSeQueL errors."""


class LocatedError(UnsequelError):
    """Base for errors that can point at a stage and/or a source position."""

    def __init__(
        self,
        message: str,
        line: int | None = None,
        column: int | None = None,
        *,
        stage_index: int | None = None,
        keyword: str | None = None,
    ) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.stage_index = stage_index
        self.keyword = keyword
        super().__init__(self._format())

    def _format(self) -> str:
        prefix = ""
        if self.stage_index is not None:
            prefix = f"stage {self.stage_index} ({self.keyword}): "
        location = ""
        if self.line is not None:
            location = f" at line {self.line}, column {self.column}"
        return f"{prefix}{self.message}{location}"


class ParseError(UnsequelError):
    """The query text is not valid UNSeQueL syntax."""


class ExecutionError(UnsequelError):
    """The query is valid but cannot be executed against the supplied data."""
