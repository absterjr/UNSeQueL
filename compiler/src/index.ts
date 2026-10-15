/**
 * Library entry: the dependency-free UNSeQueL core.
 *
 * Lexer, parser, analyzer, SQL generator, formatter, schema loader, and
 * directives — nothing here imports DuckDB, so editors, the language server,
 * and bundlers can consume the compiler without the native module. The CLI
 * (`unsequel`) and the language server (`.unsequel lsp`) build on this.
 */

export { tokenize, KEYWORDS, type Token, type TokenType } from "./lexer.js";
export { parseProgram } from "./parser.js";
export { analyze, type StageLineage } from "./semantics.js";
export { emitSql, CodegenError, type EmitOptions } from "./codegen.js";
export { formatProgram, isFormatted } from "./format.js";
export {
  loadSchema, parseSchema, typeFamily,
  type Schema, type SchemaTable, type TypeFamily,
} from "./schema.js";
export {
  loweringUnits, unitIndexFor, expressionName,
  type Program, type Stage, type StageKind, type Span, type Position, type Expr,
} from "./ast.js";
export { splitDirectives, type Directives } from "./directives.js";
export {
  PlqError, LexError, ParseError, PipelineError, SemanticError, SchemaError,
  type PipelineErrorLocation,
} from "./errors.js";
