/**
 * Shared error hierarchy for the PLQ compiler.
 *
 * Every user-facing error carries a 1-based source position. Lexer errors are
 * raised by `tokenize`; the parser (step 4) raises `ParseError` for token-level
 * failures and the stage-located `PipelineError` for ordering and shape rules.
 */

export class PlqError extends Error {
  readonly line: number;
  readonly column: number;
  /** The message without the position suffix (for re-wrapping). */
  readonly detail: string;

  constructor(message: string, line: number, column: number) {
    super(`${message} at line ${line}, column ${column}`);
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
