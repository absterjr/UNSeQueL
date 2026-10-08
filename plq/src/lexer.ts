/**
 * PLQ lexer (plan step 3; aligned to grammar v1.1 in audit batch B2).
 *
 * Turns raw `.plq` text into a flat token stream for the recursive-descent
 * parser (step 4). Follows docs/grammar.md §2:
 *
 * - stage and expression keywords are case-insensitive; tokens carry the
 *   canonical lowercase value
 * - identifiers: [A-Za-z_][A-Za-z0-9_]* (qualified names remain two
 *   identifiers separated by a `.` token; the parser assembles them)
 * - numbers: 007 or 12.5; no leading sign, no leading/trailing dot
 * - strings: single- or double-quoted, single line, escapes \\ \' \" \n,
 *   doubled quotes also escape
 * - comments: `#` or `--` to end of line, dropped
 * - newlines and `|` are emitted as tokens; the parser treats them as stage
 *   separators at parenthesis depth zero and rejects other newlines
 */

import { LexError } from "./errors.js";

export { LexError };

export type TokenType =
  | "keyword"
  | "identifier"
  | "number"
  | "string"
  | "operator"
  | "punctuation"
  | "newline"
  | "eof";

export interface Token {
  type: TokenType;
  /** Canonical lowercase for keywords; unescaped content for strings. */
  value: string;
  /** 1-based start line. */
  line: number;
  /** 1-based start column. */
  column: number;
  /** End position, exclusive: one past the last character. For `newline`
   *  tokens this is the start of the next line. Equals the start for `eof`. */
  endLine: number;
  endColumn: number;
}

export const KEYWORDS: ReadonlySet<string> = new Set([
  // stages
  "from", "join", "left", "filter", "derive", "group", "by", "aggregate",
  "having", "window", "select", "sort", "skip", "take", "distinct", "sql",
  // clauses and modifiers
  "on", "as", "over", "partition", "order", "asc", "desc",
  // expression keywords
  "and", "or", "not", "in", "like", "between", "is", "null",
  "case", "when", "then", "else", "end", "cast", "true", "false",
]);

const TWO_CHAR_OPERATORS = ["||", "<=", ">=", "!=", "<>", "=="] as const;
const SINGLE_CHAR_OPERATORS = new Set(["+", "-", "*", "/", "%", "=", "<", ">", "|"]);
const PUNCTUATION = new Set(["(", ")", ",", "."]);

const ESCAPES: Readonly<Record<string, string>> = {
  "\\": "\\",
  "'": "'",
  '"': '"',
  n: "\n",
};

export function tokenize(source: string): Token[] {
  const tokens: Token[] = [];
  let index = 0;
  let line = 1;
  let column = 1;

  const ch = (offset = 0): string => source[index + offset] ?? "";
  const advance = (count = 1): void => {
    for (let step = 0; step < count; step += 1) {
      column += 1;
      index += 1;
    }
  };
  const emit = (type: TokenType, value: string, startLine: number, startColumn: number): void => {
    tokens.push({
      type, value, line: startLine, column: startColumn,
      endLine: line, endColumn: column,
    });
  };

  while (index < source.length) {
    const current = ch();

    if (current === " " || current === "\t") {
      advance();
      continue;
    }
    if (current === "\r" || current === "\n") {
      const startLine = line;
      const startColumn = column;
      if (current === "\r" && ch(1) === "\n") index += 1;
      index += 1;
      line += 1;
      column = 1;
      tokens.push({
        type: "newline", value: "\n", line: startLine, column: startColumn,
        endLine: line, endColumn: column,
      });
      continue;
    }
    if (current === "#" || (current === "-" && ch(1) === "-")) {
      while (index < source.length && ch() !== "\n" && ch() !== "\r") advance();
      continue;
    }
    if (current === "'" || current === '"') {
      const startLine = line;
      const startColumn = column;
      const result = readString(source, index, line, column);
      column += result.end - index;
      index = result.end;
      emit("string", result.value, startLine, startColumn);
      continue;
    }
    if (isDigit(current)) {
      const startLine = line;
      const startColumn = column;
      let raw = current;
      advance();
      while (isDigit(ch())) {
        raw += ch();
        advance();
      }
      if (ch() === "." && isDigit(ch(1))) {
        raw += ".";
        advance();
        while (isDigit(ch())) {
          raw += ch();
          advance();
        }
      }
      emit("number", raw, startLine, startColumn);
      continue;
    }
    if (isIdentifierStart(current)) {
      const startLine = line;
      const startColumn = column;
      let raw = current;
      advance();
      while (isIdentifierPart(ch())) {
        raw += ch();
        advance();
      }
      const lowered = raw.toLowerCase();
      if (KEYWORDS.has(lowered)) {
        emit("keyword", lowered, startLine, startColumn);
      } else {
        emit("identifier", raw, startLine, startColumn);
      }
      continue;
    }

    const two = source.slice(index, index + 2);
    if ((TWO_CHAR_OPERATORS as readonly string[]).includes(two)) {
      const startLine = line;
      const startColumn = column;
      advance(2);
      emit("operator", two, startLine, startColumn);
      continue;
    }
    if (SINGLE_CHAR_OPERATORS.has(current)) {
      const startLine = line;
      const startColumn = column;
      advance();
      emit("operator", current, startLine, startColumn);
      continue;
    }
    if (PUNCTUATION.has(current)) {
      const startLine = line;
      const startColumn = column;
      advance();
      emit("punctuation", current, startLine, startColumn);
      continue;
    }
    throw new LexError(`unexpected character ${JSON.stringify(current)}`, line, column);
  }

  tokens.push({
    type: "eof", value: "", line, column, endLine: line, endColumn: column,
  });
  return tokens;
}

function isDigit(value: string): boolean {
  return value >= "0" && value <= "9";
}

function isIdentifierStart(value: string): boolean {
  return (value >= "a" && value <= "z") || (value >= "A" && value <= "Z") || value === "_";
}

function isIdentifierPart(value: string): boolean {
  return isIdentifierStart(value) || isDigit(value);
}

function readString(
  source: string,
  start: number,
  line: number,
  column: number,
): { value: string; end: number } {
  const quote = source[start] ?? "";
  let i = start + 1;
  let value = "";
  while (i < source.length) {
    const current = source[i] ?? "";
    if (current === "\n" || current === "\r") {
      throw new LexError("unterminated string (strings are single-line)", line, column);
    }
    if (current === "\\") {
      const escaped = source[i + 1];
      if (escaped === undefined) {
        throw new LexError("dangling backslash at end of input", line, column + (i - start));
      }
      const replacement = ESCAPES[escaped];
      if (replacement === undefined) {
        throw new LexError(`unknown escape sequence \\${escaped}`, line, column + (i - start));
      }
      value += replacement;
      i += 2;
      continue;
    }
    if (current === quote) {
      if (source[i + 1] === quote) {
        value += quote;
        i += 2;
        continue;
      }
      return { value, end: i + 1 };
    }
    value += current;
    i += 1;
  }
  throw new LexError("unterminated string", line, column);
}
