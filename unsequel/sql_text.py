"""Small scanners for SQL text embedded in a `sql` stage.

The escape hatch accepts raw SQL; these helpers let the parser and codegen
inspect it without shipping a SQL parser: find the first keyword, detect a
statement terminator outside literals and comments, and skip quoted runs.
"""

from __future__ import annotations

import re

_FIRST_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def scan_quoted(text: str, start: int, quote: str) -> int:
    """Return the index just past the quoted run that starts at `start`."""
    i = start + 1
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            i += 2
            continue
        if char == quote:
            if i + 1 < len(text) and text[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return len(text)


def first_word(text: str) -> str:
    """First keyword of the statement, skipping whitespace and comments."""
    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if text.startswith("--", i):
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = len(text) if close < 0 else close + 2
            continue
        break
    match = _FIRST_WORD.match(text, i)
    return match.group(0).upper() if match else ""


def has_terminator(text: str) -> bool:
    """True if a ';' appears outside strings and comments."""
    i = 0
    while i < len(text):
        char = text[i]
        two = text[i:i + 2]
        if two == "--":
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline + 1
            continue
        if two == "/*":
            close = text.find("*/", i + 2)
            i = len(text) if close < 0 else close + 2
            continue
        if char in ("'", '"'):
            i = scan_quoted(text, i, char)
            continue
        if char == ";":
            return True
        i += 1
    return False
