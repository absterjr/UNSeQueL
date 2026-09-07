class UnsequelError(Exception):
    """Base class for user-facing UNSeQueL errors."""


class ParseError(UnsequelError):
    """The query text is not valid UNSeQueL syntax."""


class ExecutionError(UnsequelError):
    """The query is valid but cannot be executed against the supplied data."""
