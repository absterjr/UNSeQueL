/**
 * Shared error hierarchy for the PLQ compiler.
 *
 * Source-located errors carry a 1-based position; schema-file errors carry
 * none. `LexError` comes from `tokenize`, `ParseError` from the parser,
 * `PipelineError` covers ordering rules, `SemanticError` extends it for
 * schema-aware validation, and `SchemaError` reports malformed schema files.
 */

export class PlqError extends Error {
  readonly line: number | undefined;
  readonly column: number | undefined;
  /** The message without the position suffix (for re-wrapping). */
  readonly detail: string;

  constructor(message: string, line?: number, column?: number) {
    super(line !== undefined && column !== undefined
      ? `${message} at line ${line}, column ${column}`
      : message);
    this.name = "PlqError";
    this.line = line;
    this.column = column;
    this.detail = message;
  }
}

export class LexError extends PlqError {
  constructor(message: string, line: number, column: number) {
    super(message, line, column);
    this.name = "LexError";
  }
}

export class ParseError extends PlqError {
  constructor(message: string, line: number, column: number) {
    super(message, line, column);
    this.name = "ParseError";
  }
}

export interface PipelineErrorLocation {
  /** 1-based grammar stage ordinal (docs/grammar.md §1.1). */
  index: number;
  /** Canonical stage keyword, e.g. "join" or "group by". */
  keyword: string;
  line: number;
  column: number;
}

export class PipelineError extends PlqError {
  readonly stageIndex: number;
  readonly stageKeyword: string;

  constructor(message: string, location: PipelineErrorLocation) {
    super(`stage ${location.index} (${location.keyword}): ${message}`,
          location.line, location.column);
    this.name = "PipelineError";
    this.stageIndex = location.index;
    this.stageKeyword = location.keyword;
  }
}

export class SemanticError extends PipelineError {
  constructor(message: string, location: PipelineErrorLocation) {
    super(message, location);
    this.name = "SemanticError";
  }
}

export class SchemaError extends PlqError {
  constructor(message: string) {
    super(message);
    this.name = "SchemaError";
  }
}
