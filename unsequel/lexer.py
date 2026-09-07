from __future__ import annotations

from dataclasses import dataclass

from .errors import ParseError


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    position: int


def _location(text: str, position: int) -> str:
    line = text.count("\n", 0, position) + 1
    last_newline = text.rfind("\n", 0, position)
    column = position + 1 if last_newline < 0 else position - last_newline
    return f"line {line}, column {column}"


def _read_string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    i = start + 1
    chars: list[str] = []
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            chars.append(text[i + 1])
            i += 2
            continue
        if char == quote:
            if i + 1 < len(text) and text[i + 1] == quote:
                chars.append(quote)
                i += 2
                continue
            return "".join(chars), i + 1
        chars.append(char)
        i += 1
    raise ParseError(f"Unterminated string at {_location(text, start)}")


def tokenize(text: str) -> list[Token]:
    """Convert query text into small tokens for the expression/parser layers."""
    tokens: list[Token] = []
    i = 0
    two_char_ops = {"<=", ">=", "!=", "<>"}
    single_char_ops = set("+-*/%()=<>.,;")

    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if text.startswith("--", i) or char == "#":
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline + 1
            continue
        if char in ("'", '"'):
            value, i = _read_string(text, i)
            tokens.append(Token("STRING", value, i))
            continue
        if char.isdigit():
            start = i
            i += 1
            while i < len(text) and (text[i].isdigit() or text[i] == "."):
                i += 1
            raw = text[start:i]
            value: int | float = float(raw) if "." in raw else int(raw)
            tokens.append(Token("NUMBER", value, start))
            continue
        if char.isalpha() or char == "_":
            start = i
            i += 1
            while i < len(text) and (text[i].isalnum() or text[i] in "_$"):
                i += 1
            tokens.append(Token("IDENT", text[start:i], start))
            continue
        if text[i:i + 2] in two_char_ops:
            tokens.append(Token("OP", text[i:i + 2], i))
            i += 2
            continue
        if char in single_char_ops:
            tokens.append(Token("OP", char, i))
            i += 1
            continue
        raise ParseError(f"Unexpected character {char!r} at {_location(text, i)}")

    tokens.append(Token("EOF", "", len(text)))
    return tokens
