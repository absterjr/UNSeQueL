/**
 * DuckDB SQL code generation (plan step 6).
 *
 * `emitSql(program, schema)` lowers the stage IR to a `WITH` chain where each
 * lowering unit (grammar §1.1) becomes one CTE: every stage is its own unit
 * except `group by` + `aggregate`, which share one. `stopAt` truncates the
 * program at a 1-based grammar stage ordinal, keeping the whole unit that
 * contains it — the basis for stage preview.
 */

import {
  expressionName,
  type AggregateStage,
  type BetweenExpr,
  type BinaryExpr,
  type CaseExpr,
  type CastExpr,
  type DeriveStage,
  type Expr,
  type FilterStage,
  type FromStage,
  type FuncCallExpr,
  type GroupByStage,
  type HavingStage,
  type InExpr,
  type IsNullExpr,
  type JoinStage,
  type LikeExpr,
  type Program,
  type RawSqlStage,
  type SelectStage,
  type SkipStage,
  type SortKey,
  type SortStage,
  type Stage,
  type TakeStage,
  type UnaryExpr,
  type WindowStage,
} from "./ast.js";
import { PlqError } from "./errors.js";
import type { Schema } from "./schema.js";

export class CodegenError extends PlqError {
  constructor(message: string, line?: number, column?: number) {
    super(message, line, column);
    this.name = "CodegenError";
  }
}

export interface EmitOptions {
  /** Keep only the unit containing this 1-based grammar stage ordinal. */
  readonly stopAt?: number;
}

const SAFE_IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/;

// DuckDB reserved words: identifiers that must be quoted when emitted.
const SQL_KEYWORDS = new Set([
  "all", "and", "anti", "any", "array", "as", "asc", "between", "both", "by",
  "case", "cast", "check", "column", "constraint", "create", "cross",
  "current", "default", "delete", "desc", "distinct", "do", "drop", "else",
  "end", "except", "exists", "false", "fetch", "filter", "for", "foreign",
  "from", "full", "function", "grant", "group", "having", "in", "inner",
  "insert", "intersect", "into", "is", "join", "lateral", "left", "like",
  "limit", "natural", "not", "null", "offset", "on", "or", "order", "outer",
  "over", "partition", "primary", "qualify", "range", "right", "row",
  "rows", "select", "set", "some", "table", "then", "to", "true", "trailing",
  "union", "unique", "update", "using", "values", "when", "where", "window",
  "with",
]);

const INPUT_NAME = /\b__input__\b/;

function ident(name: string): string {
  if (SAFE_IDENT.test(name) && !SQL_KEYWORDS.has(name.toLowerCase())) return name;
  return `"${name.replace(/"/g, '""')}"`;
}

function qualified(name: string): string {
  return name.split(".").map(ident).join(".");
}

function stageError(stage: Stage, message: string): CodegenError {
  return new CodegenError(
    `stage ${stage.index} (${stage.keyword}): ${message}`,
    stage.span.start.line,
    stage.span.start.column,
  );
}

/** Resolves a program-level column name to a SQL token for one CTE. */
class Cols {
  readonly lookup: Map<string, string>;
  private readonly opaque: boolean;

  constructor(lookup: Map<string, string> = new Map(), opaque = false) {
    this.lookup = lookup;
    this.opaque = opaque;
  }

  resolve(name: string): string {
    const direct = this.lookup.get(name);
    if (direct !== undefined) return direct;
    if (this.opaque) {
      // After a sql stage the columns are the author's responsibility.
      return qualified(name);
    }
    throw new CodegenError(`cannot resolve column '${name}' in this stage`);
  }

  static flat(names: readonly string[]): Cols {
    return new Cols(new Map(names.map((name) => [name, ident(name)])));
  }
}

function aliased(rendered: string, name: string): string {
  return rendered === ident(name) ? rendered : `${rendered} AS ${ident(name)}`;
}

// --------------------------------------------------------------------------- //
// Expression rendering
// --------------------------------------------------------------------------- //

function literal(value: string | number | boolean | null): string {
  if (value === null) return "NULL";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  if (typeof value === "number") return String(value);
  return `'${value.replace(/'/g, "''")}'`;
}

function render(expr: Expr, cols: Cols): string {
  switch (expr.kind) {
    case "literal":
      return literal(expr.value);
    case "name":
      return cols.resolve(expr.name);
    case "unary": {
      const operand = render(expr.operand, cols);
      return expr.op === "not" ? `(NOT ${operand})` : `(${expr.op}${operand})`;
    }
    case "binary": {
      const op = expr.op.toUpperCase();
      return `(${render(expr.left, cols)} ${op} ${render(expr.right, cols)})`;
    }
    case "like": {
      const keyword = expr.negated ? "NOT LIKE" : "LIKE";
      return `(${render(expr.value, cols)} ${keyword} ${render(expr.pattern, cols)})`;
    }
    case "in": {
      const keyword = expr.negated ? "NOT IN" : "IN";
      const options = expr.options.map((option) => render(option, cols)).join(", ");
      return `(${render(expr.value, cols)} ${keyword} (${options}))`;
    }
    case "between": {
      const keyword = expr.negated ? "NOT BETWEEN" : "BETWEEN";
      return `(${render(expr.value, cols)} ${keyword} `
        + `${render(expr.lower, cols)} AND ${render(expr.upper, cols)})`;
    }
    case "is null": {
      const keyword = expr.negated ? "IS NOT NULL" : "IS NULL";
      return `(${render(expr.value, cols)} ${keyword})`;
    }
    case "case": {
      const parts = ["CASE"];
      for (const branch of expr.branches) {
        parts.push(`WHEN ${render(branch.when, cols)} THEN ${render(branch.then, cols)}`);
      }
      if (expr.elseExpr !== null) parts.push(`ELSE ${render(expr.elseExpr, cols)}`);
      parts.push("END");
      return `(${parts.join(" ")})`;
    }
    case "cast":
      return `CAST(${render(expr.operand, cols)} AS ${expr.typeName})`;
    case "call": {
      if (expr.star) return `${expr.name.toUpperCase()}(*)`;
      const prefix = expr.distinct ? "DISTINCT " : "";
      const args = expr.args.map((arg) => render(arg, cols)).join(", ");
      return `${expr.name.toUpperCase()}(${prefix}${args})`;
    }
  }
}

function renderWindow(call: WindowStage["call"], cols: Cols): string {
  const args = call.star ? "*" : call.args.map((arg) => render(arg, cols)).join(", ");
  const parts: string[] = [];
  if (call.partitionBy.length > 0) {
    parts.push(`PARTITION BY ${call.partitionBy.map((expr) => render(expr, cols)).join(", ")}`);
  }
  if (call.orderBy.length > 0) {
    parts.push(`ORDER BY ${call.orderBy.map((key) => renderSortKey(key, cols)).join(", ")}`);
  }
  return `${call.func.toUpperCase()}(${args}) OVER (${parts.join(" ")})`;
}

function renderSortKey(key: SortKey, cols: Cols): string {
  return `${render(key.expr, cols)}${key.descending ? " DESC" : ""}`;
}

// --------------------------------------------------------------------------- //
// __input__ substitution (strings, comments, and quoted identifiers aware)
// --------------------------------------------------------------------------- //

function scanQuoted(text: string, start: number, quote: string): number {
  let i = start + 1;
  while (i < text.length) {
    const char = text[i] as string;
    if (char === "\\" && i + 1 < text.length) {
      i += 2;
      continue;
    }
    if (char === quote) {
      if (text[i + 1] === quote) {
        i += 2;
        continue;
      }
      return i + 1;
    }
    i += 1;
  }
  return text.length;
}

function substituteInput(text: string, replacement: string): string {
  const out: string[] = [];
  let i = 0;
  while (i < text.length) {
    const char = text[i] as string;
    const two = text.slice(i, i + 2);
    if (two === "--") {
      const newline = text.indexOf("\n", i);
      const end = newline < 0 ? text.length : newline;
      out.push(text.slice(i, end));
      i = end;
      continue;
    }
    if (two === "/*") {
      const close = text.indexOf("*/", i + 2);
      const end = close < 0 ? text.length : close + 2;
      out.push(text.slice(i, end));
      i = end;
      continue;
    }
    if (char === "'") {
      const end = scanQuoted(text, i, "'");
      out.push(text.slice(i, end));
      i = end;
      continue;
    }
    if (char === '"') {
      const end = scanQuoted(text, i, '"');
      if (text.slice(i + 1, end - 1) === "__input__") {
        out.push(replacement);
      } else {
        out.push(text.slice(i, end));
      }
      i = end;
      continue;
    }
    const match = INPUT_NAME.exec(text.slice(i, i + 16));
    if (match !== null && text.startsWith("__input__", i)) {
      out.push(replacement);
      i += "__input__".length;
      continue;
    }
    out.push(char);
    i += 1;
  }
  return out.join("");
}

// --------------------------------------------------------------------------- //
// Stage lowering
// --------------------------------------------------------------------------- //

interface StageResult {
  sql: string;
  cols: Cols;
  live: readonly string[];
}

function labelOf(source: { name: string; alias: string | null }): string {
  return source.alias ?? source.name.split(".").pop() ?? source.name;
}

function sourceSql(source: { name: string; alias: string | null }): string {
  const name = qualified(source.name);
  return source.alias === null ? name : `${name} AS ${ident(source.alias)}`;
}

function fromStage(stage: FromStage, schema: Schema): StageResult {
  const table = schema.tables.get(stage.source.name);
  if (table === undefined) throw stageError(stage, `unknown table '${stage.source.name}'`);
  const label = labelOf(stage.source);
  const lookup = new Map<string, string>();
  for (const column of table.columns.keys()) {
    lookup.set(column, ident(column));
    lookup.set(`${label}.${column}`, ident(column));
  }
  return {
    sql: `SELECT * FROM ${sourceSql(stage.source)}`,
    cols: new Cols(lookup),
    live: [...table.columns.keys()],
  };
}

function joinStage(stage: JoinStage, schema: Schema, prevCte: string,
                   prev: Cols, live: readonly string[]): StageResult {
  const table = schema.tables.get(stage.source.name);
  if (table === undefined) throw stageError(stage, `unknown joined table '${stage.source.name}'`);
  const label = labelOf(stage.source);

  // ON runs in the join's own scope: bare names resolve to the previous CTE,
  // qualified names to whichever side owns them (left labels included).
  const onLookup = new Map<string, string>();
  for (const name of live) {
    onLookup.set(name, `${prevCte}.${ident(name)}`);
    onLookup.set(`${prevCte}.${name}`, `${prevCte}.${ident(name)}`);
  }
  for (const [name, exposed] of prev.lookup) {
    if (name.includes(".")) onLookup.set(name, `${prevCte}.${exposed}`);
  }
  for (const column of table.columns.keys()) {
    onLookup.set(`${label}.${column}`, `${ident(label)}.${ident(column)}`);
  }
  const onSql = render(stage.on, new Cols(onLookup));

  const taken = new Set(live);
  const projected: string[] = [`${prevCte}.*`];
  const lookup = new Map(prev.lookup);
  const newLive = [...live];
  for (const column of table.columns.keys()) {
    const exposed = taken.has(column) ? `${label}_${column}` : column;
    projected.push(exposed === column
      ? `${ident(label)}.${ident(column)}`
      : `${ident(label)}.${ident(column)} AS ${ident(exposed)}`);
    taken.add(exposed);
    newLive.push(exposed);
    if (!lookup.has(column)) lookup.set(column, ident(exposed));
    lookup.set(`${label}.${column}`, ident(exposed));
  }

  const keyword = stage.joinKind === "left" ? "LEFT JOIN" : "JOIN";
  return {
    sql: `SELECT ${projected.join(", ")} FROM ${prevCte} ${keyword} `
      + `${sourceSql(stage.source)} ON ${onSql}`,
    cols: new Cols(lookup),
    live: newLive,
  };
}

function deriveStage(stage: DeriveStage, prevCte: string, cols: Cols,
                     live: readonly string[]): StageResult {
  for (const item of stage.items) {
    if (live.includes(item.name)) {
      throw stageError(stage,
        `derive '${item.name}' conflicts with a live column; choose another name`);
    }
  }
  const additions = stage.items
    .map((item) => aliased(render(item.expr, cols), item.name))
    .join(", ");
  const lookup = new Map(cols.lookup);
  for (const item of stage.items) lookup.set(item.name, ident(item.name));
  return {
    sql: `SELECT *, ${additions} FROM ${prevCte}`,
    cols: new Cols(lookup),
    live: [...live, ...stage.items.map((item) => item.name)],
  };
}

function groupUnitSql(group: GroupByStage, aggregate: AggregateStage,
                      prevCte: string, cols: Cols): StageResult {
  const keys = group.keys.map((key) => aliased(render(key.expr, cols), key.name));
  const groupBy = group.keys.map((key) => render(key.expr, cols)).join(", ");
  const aggregates = aggregate.items
    .map((item) => aliased(render(item.call, cols), item.name))
    .join(", ");
  const names = [
    ...group.keys.map((key) => key.name),
    ...aggregate.items.map((item) => item.name),
  ];
  return {
    sql: `SELECT ${[...keys, aggregates].join(", ")} FROM ${prevCte} GROUP BY ${groupBy}`,
    cols: Cols.flat(names),
    live: names,
  };
}

function singleStageSql(stage: Stage, prevCte: string, cols: Cols,
                        live: readonly string[]): StageResult {
  switch (stage.kind) {
    case "filter":
    case "having":
      return { sql: `SELECT * FROM ${prevCte} WHERE ${render(stage.condition, cols)}`, cols, live };
    case "window": {
      const lookup = new Map(cols.lookup);
      lookup.set(stage.name, ident(stage.name));
      return {
        sql: `SELECT *, ${renderWindow(stage.call, cols)} AS ${ident(stage.name)} FROM ${prevCte}`,
        cols: new Cols(lookup),
        live: [...live, stage.name],
      };
    }
    case "select": {
      if (stage.star) return { sql: `SELECT * FROM ${prevCte}`, cols, live };
      const names = stage.items.map((item) => item.alias ?? expressionName(item.expr));
      const rendered = stage.items
        .map((item, i) => aliased(render(item.expr, cols), names[i] as string))
        .join(", ");
      return { sql: `SELECT ${rendered} FROM ${prevCte}`, cols: Cols.flat(names), live: names };
    }
    case "sort": {
      const keys = stage.keys.map((key) => renderSortKey(key, cols)).join(", ");
      return { sql: `SELECT * FROM ${prevCte} ORDER BY ${keys}`, cols, live };
    }
    case "skip": {
      const skip = stage as SkipStage;
      return { sql: `SELECT * FROM ${prevCte} OFFSET ${skip.count}`, cols, live };
    }
    case "take": {
      const take = stage as TakeStage;
      return { sql: `SELECT * FROM ${prevCte} LIMIT ${take.count}`, cols, live };
    }
    case "distinct":
      return { sql: `SELECT DISTINCT * FROM ${prevCte}`, cols, live };
    case "sql": {
      const raw = stage as RawSqlStage;
      const text = substituteInput(raw.text, prevCte);
      const indented = text.split("\n").join("\n  ");
      return { sql: indented, cols: new Cols(new Map(), true), live: [] };
    }
    default:
      throw stageError(stage, `cannot lower stage '${stage.keyword}'`);
  }
}

// --------------------------------------------------------------------------- //
// Program lowering
// --------------------------------------------------------------------------- //

function truncate(program: Program, stopAt: number | undefined): Program {
  if (stopAt === undefined) return program;
  if (!Number.isInteger(stopAt) || stopAt < 1) {
    throw new CodegenError("stopAt must be a positive grammar stage ordinal");
  }
  const limit = Math.min(stopAt, program.length);
  const stages = program.slice(0, limit);
  const last = stages[stages.length - 1];
  const next = program[limit];
  if (last?.kind === "group by" && next?.kind === "aggregate") {
    return [...stages, next];
  }
  return stages;
}

export function emitSql(program: Program, schema: Schema, options: EmitOptions = {}): string {
  const stages = truncate(program, options.stopAt);
  if (stages.length === 0 || stages[0]?.kind !== "from") {
    throw new CodegenError("a program must start with a from stage");
  }

  const ctes: Array<{ name: string; sql: string }> = [];
  let cols = new Cols();
  let live: readonly string[] = [];
  let position = 0;
  let ordinal = 0;

  while (position < stages.length) {
    const stage = stages[position] as Stage;
    ordinal += 1;
    const prevCte = `stage_${ordinal - 1}`;
    let result: StageResult;
    switch (stage.kind) {
      case "from":
        result = fromStage(stage, schema);
        break;
      case "join":
        result = joinStage(stage, schema, prevCte, cols, live);
        break;
      case "group by": {
        const aggregate = stages[position + 1];
        if (aggregate === undefined || aggregate.kind !== "aggregate") {
          throw stageError(stage, "group by must be immediately followed by aggregate");
        }
        result = groupUnitSql(stage, aggregate, prevCte, cols);
        position += 1;
        break;
      }
      case "derive":
        result = deriveStage(stage, prevCte, cols, live);
        break;
      default:
        result = singleStageSql(stage, prevCte, cols, live);
        break;
    }
    ctes.push({ name: `stage_${ordinal}`, sql: result.sql });
    cols = result.cols;
    live = result.live;
    position += 1;
  }

  const body = ctes.map((cte) => `${cte.name} AS (\n  ${cte.sql}\n)`).join(",\n");
  return `WITH ${body}\nSELECT * FROM stage_${ctes.length};`;
}
