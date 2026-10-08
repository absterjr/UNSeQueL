/**
 * Shared error hierarchy for the PLQ compiler.
 *
 * Every user-facing error carries a 1-based source position. Lexer errors are
 * defined here; the parser (step 4) adds `ParseError` and the stage-located
 * `PipelineError` on top of the same base.
 */

export class PlqError extends Error {
  readonly line: number;
  readonly column: number;

  constructor(message: string, line: number, column: number) {
    super(`${message} at line ${line}, column ${column}`);
    this.name = "PlqError";
    this.line = line;
    this.column = column;
  }
}

export class LexError extends PlqError {
  constructor(message: string, line: number, column: number) {
    super(message, line, column);
    this.name = "LexError";
  }
}
