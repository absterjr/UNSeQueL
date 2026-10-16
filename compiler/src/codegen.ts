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
import { withCreatedTable } from "./schema.js";

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

const INPUT_NAME_LENGTH = "__input__".length;

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
  readonly opaque: boolean;
  readonly ambiguous: ReadonlySet<string>;

  constructor(lookup: Map<string, string> = new Map(), opaque = false,
              ambiguous: ReadonlySet<string> = new Set()) {
    this.lookup = lookup;
    this.opaque = opaque;
    this.ambiguous = ambiguous;
  }

  resolve(name: string): string {
    if (!name.includes(".") && this.ambiguous.has(name.toLowerCase())) {
      throw new CodegenError(
        `column '${name}' is ambiguous after a join; qualify it with a source label`);
    }
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

function literal(value: string | number | boolean | null, raw?: string): string {
  if (value === null) return "NULL";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  // Number literals keep their source text: a 20-digit ID must not round.
  if (typeof value === "number") return raw ?? String(value);
  return `'${value.replace(/'/g, "''")}'`;
}

function render(expr: Expr, cols: Cols): string {
  switch (expr.kind) {
    case "literal":
      return literal(expr.value, expr.raw);
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
    if (text.startsWith("__input__", i)
        && !isWordChar(text[i - 1])
        && !isWordChar(text[i + INPUT_NAME_LENGTH])) {
      out.push(replacement);
      i += INPUT_NAME_LENGTH;
      continue;
    }
    out.push(char);
    i += 1;
  }
  return out.join("");
}

function isWordChar(char: string | undefined): boolean {
  return char !== undefined && /[A-Za-z0-9_]/.test(char);
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

interface ResolvedRight {
  sql: string;
  label: string;
  columns: readonly string[];
}

function rightForTable(stage: JoinStage, schema: Schema): ResolvedRight {
  const table = schema.tables.get(stage.source.name);
  if (table === undefined) throw stageError(stage, `unknown joined table '${stage.source.name}'`);
  return {
    sql: sourceSql(stage.source),
    label: labelOf(stage.source),
    columns: [...table.columns.keys()],
  };
}

function joinStage(stage: JoinStage, prevCte: string, prev: Cols, live: readonly string[],
                   right: ResolvedRight): StageResult {
  const label = right.label;

  // ON runs in the join's own scope: bare names resolve to the previous CTE,
  // qualified names to whichever side owns them (left labels included).
  const onLookup = new Map<string, string>();
  const onAmbiguous = new Set(prev.ambiguous);
  for (const name of live) {
    onLookup.set(name, `${prevCte}.${ident(name)}`);
    onLookup.set(`${prevCte}.${name}`, `${prevCte}.${ident(name)}`);
  }
  for (const [name, exposed] of prev.lookup) {
    if (name.includes(".")) onLookup.set(name, `${prevCte}.${exposed}`);
  }
  for (const column of right.columns) {
    onLookup.set(`${label}.${column}`, `${ident(label)}.${ident(column)}`);
    if (live.some((name) => name.toLowerCase() === column.toLowerCase())) {
      onAmbiguous.add(column.toLowerCase());
    }
  }
  const onSql = stage.joinKind === "cross"
    ? ""
    : render(stage.on, new Cols(onLookup, false, onAmbiguous));

  const taken = new Set(live.map((name) => name.toLowerCase()));
  const ambiguous = new Set(prev.ambiguous);
  const projected: string[] = [`${prevCte}.*`];
  const lookup = new Map(prev.lookup);
  const newLive = [...live];
  for (const column of right.columns) {
    const collision = taken.has(column.toLowerCase());
    if (collision) ambiguous.add(column.toLowerCase());
    const exposed = collision ? `${label}_${column}` : column;
    if (taken.has(exposed.toLowerCase())) {
      throw stageError(stage,
        `join exposes duplicate column '${exposed}'; alias one of the joins`);
    }
    projected.push(exposed === column
      ? `${ident(label)}.${ident(column)}`
      : `${ident(label)}.${ident(column)} AS ${ident(exposed)}`);
    taken.add(exposed.toLowerCase());
    newLive.push(exposed);
    if (!lookup.has(column)) lookup.set(column, ident(exposed));
    lookup.set(`${label}.${column}`, ident(exposed));
  }

  const keyword = {
    inner: "JOIN",
    left: "LEFT JOIN",
    right: "RIGHT JOIN",
    full: "FULL OUTER JOIN",
    cross: "CROSS JOIN",
  }[stage.joinKind];
  return {
    sql: `SELECT ${projected.join(", ")} FROM ${prevCte} ${keyword} `
      + `${right.sql}${stage.joinKind === "cross" ? "" : ` ON ${onSql}`}`,
    cols: new Cols(lookup, false, ambiguous),
    live: newLive,
  };
}

/** `SELECT * FROM <cte> AS <label>` with the label's columns addressable. */
function fromResolved(sql: string, label: string, live: readonly string[],
                      source: Cols): StageResult {
  const lookup = new Map<string, string>();
  for (const name of live) {
    lookup.set(name, ident(name));
    if (label !== "") lookup.set(`${label}.${name}`, ident(name));
  }
  const aliased = label === "" ? sql : `${sql} AS ${ident(label)}`;
  return {
    sql: `SELECT * FROM ${aliased}`,
    cols: new Cols(lookup, source.opaque, source.ambiguous),
    live: [...live],
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
  // Items resolve left-to-right. SQL aliases are not visible inside one SELECT
  // list, so an earlier item is inlined into later item expressions.
  const scope = new Map(cols.lookup);
  const additions: string[] = [];
  for (const item of stage.items) {
    const rendered = render(item.expr, new Cols(scope, cols.opaque, cols.ambiguous));
    additions.push(aliased(rendered, item.name));
    // Rendered binary/case expressions already come parenthesized.
    scope.set(item.name, rendered);
  }
  const lookup = new Map(cols.lookup);
  for (const item of stage.items) lookup.set(item.name, ident(item.name));
  return {
    sql: `SELECT *, ${additions.join(", ")} FROM ${prevCte}`,
    cols: new Cols(lookup, cols.opaque, cols.ambiguous),
    live: [...live, ...stage.items.map((item) => item.name)],
  };
}

function groupUnitSql(group: GroupByStage, aggregate: AggregateStage,
                      prevCte: string, cols: Cols): StageResult {
  const renderedKeys = group.keys.map((key) => render(key.expr, cols));
  const keys = group.keys.map((key, index) =>
    aliased(renderedKeys[index] as string, key.name));
  const groupBy = renderedKeys.join(", ");
  const aggregates = aggregate.items
    .map((item) => aliased(render(item.call, cols), item.name))
    .join(", ");
  const names = [
    ...group.keys.map((key) => key.name),
    ...aggregate.items.map((item) => item.name),
  ];
  const lookup = Cols.flat(names).lookup;
  for (let index = 0; index < group.keys.length; index += 1) {
    const key = group.keys[index] as GroupByStage["keys"][number];
    const rendered = renderedKeys[index];
    for (const [inputName, sqlName] of cols.lookup) {
      if (inputName.includes(".") && sqlName === rendered) {
        lookup.set(inputName, ident(key.name));
      }
    }
  }
  return {
    sql: `SELECT ${[...keys, aggregates].join(", ")} FROM ${prevCte} GROUP BY ${groupBy}`,
    cols: new Cols(lookup),
    live: names,
  };
}

function singleStageSql(stage: Stage, prevCte: string, cols: Cols,
                        live: readonly string[], orderColumn: string | null): StageResult {
  switch (stage.kind) {
    case "filter":
    case "having":
      return { sql: `SELECT * FROM ${prevCte} WHERE ${render(stage.condition, cols)}`, cols, live };
    case "window": {
      if (live.includes(stage.name)) {
        throw stageError(stage,
          `window '${stage.name}' conflicts with a live column; choose another name`);
      }
      const lookup = new Map(cols.lookup);
      lookup.set(stage.name, ident(stage.name));
      return {
        sql: `SELECT *, ${renderWindow(stage.call, cols)} AS ${ident(stage.name)} FROM ${prevCte}`,
        cols: new Cols(lookup, cols.opaque, cols.ambiguous),
        live: [...live, stage.name],
      };
    }
    case "select": {
      if (stage.star && stage.items.length === 0) {
        return { sql: `SELECT * FROM ${prevCte}`, cols: Cols.flat(live), live };
      }
      const itemNames = stage.items.map((item) => item.alias ?? expressionName(item.expr));
      if (stage.star) {
        const liveNames = new Set(live.map((name) => name.toLowerCase()));
        const duplicate = itemNames.find((name) => liveNames.has(name.toLowerCase()));
        if (duplicate !== undefined) {
          throw stageError(stage,
            `select output '${duplicate}' duplicates a live column from '*'; choose a different alias`);
        }
      }
      const names = stage.star ? [...live, ...itemNames] : itemNames;
      const rendered = stage.items
        .map((item, i) => aliased(render(item.expr, cols), itemNames[i] as string));
      if (stage.star) rendered.unshift("*");
      if (orderColumn !== null && !stage.star) {
        rendered.push(`${prevCte}.${ident(orderColumn)} AS ${ident(orderColumn)}`);
      }
      return { sql: `SELECT ${rendered.join(", ")} FROM ${prevCte}`,
        cols: Cols.flat(names), live: names };
    }
    case "skip": {
      const skip = stage as SkipStage;
      const order = orderColumn === null ? "" : ` ORDER BY ${ident(orderColumn)}`;
      return { sql: `SELECT * FROM ${prevCte}${order} OFFSET ${skip.count}`, cols, live };
    }
    case "take": {
      const take = stage as TakeStage;
      const order = orderColumn === null ? "" : ` ORDER BY ${ident(orderColumn)}`;
      return { sql: `SELECT * FROM ${prevCte}${order} LIMIT ${take.count}`, cols, live };
    }
    case "distinct": {
      const exclusion = orderColumn === null ? "" : ` EXCLUDE (${ident(orderColumn)})`;
      return { sql: `SELECT DISTINCT *${exclusion} FROM ${prevCte}`, cols, live };
    }
    case "sql": {
      const raw = stage as RawSqlStage;
      const input = orderColumn === null
        ? prevCte
        : `(SELECT * EXCLUDE (${ident(orderColumn)}) FROM ${prevCte})`;
      const text = substituteInput(raw.text, input);
      const indented = text.split("\n").join("\n  ");
      return { sql: indented, cols: new Cols(new Map(), true), live: [] };
    }
    default:
      throw stageError(stage, `cannot lower stage '${stage.keyword}'`);
  }
}

function sortStageSql(stage: SortStage, prevCte: string, cols: Cols,
                      orderColumn: string | null, nextOrderColumn: string): string {
  let keys: string;
  try {
    const rendered = stage.keys.map((key) => renderSortKey(key, cols));
    if (orderColumn !== null) rendered.push(`${ident(orderColumn)} ASC`);
    keys = rendered.join(", ");
  } catch (error) {
    if (error instanceof CodegenError) {
      throw stageError(stage,
        `sort key is no longer available after an earlier projection; sort again after select`);
    }
    throw error;
  }
  const input = orderColumn === null
    ? "*"
    : `* EXCLUDE (${ident(orderColumn)})`;
  return `SELECT ${input}, ROW_NUMBER() OVER (ORDER BY ${keys}) AS `
    + `${ident(nextOrderColumn)} FROM ${prevCte}`;
}

function internalOrderNames(program: Program, schema: Schema): Set<string> {
  const names = new Set<string>();
  for (const table of schema.tables.values()) {
    for (const column of table.columns.keys()) names.add(column.toLowerCase());
  }
  const collect = (stages: Program): void => {
    for (const stage of stages) {
      switch (stage.kind) {
        case "with":
        case "set":
          collect(stage.program);
          break;
        case "from":
        case "join":
          if (stage.source.alias !== null) names.add(stage.source.alias.toLowerCase());
          if (stage.source.program !== undefined) collect(stage.source.program);
          break;
        case "derive":
          for (const item of stage.items) names.add(item.name.toLowerCase());
          break;
        case "group by":
          for (const item of stage.keys) names.add(item.name.toLowerCase());
          break;
        case "aggregate":
          for (const item of stage.items) names.add(item.name.toLowerCase());
          break;
        case "window":
          names.add(stage.name.toLowerCase());
          break;
        case "select":
          for (const item of stage.items) {
            names.add((item.alias ?? expressionName(item.expr)).toLowerCase());
          }
          break;
        case "sql":
          names.add(stage.text.toLowerCase());
          break;
      }
    }
  };
  collect(program);
  return names;
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

interface ChainState {
  lastCte: string;
  cols: Cols;
  live: readonly string[];
  orderColumn: string | null;
}

interface ChainContext {
  schema: Schema;
  ctes: Array<{ name: string; sql: string }>;
  nextOrderName: () => string;
}

function buildChain(stages: Program, context: ChainContext, prefix: string,
                    nested: boolean, inheritedWiths: Map<string, ChainState>): ChainState {
  const withNames = new Map(inheritedWiths);
  let state: ChainState | null = null;
  let orderColumn: string | null = null;
  let position = 0;
  let ordinal = 0;

  while (position < stages.length) {
    const stage = stages[position] as Stage;
    if (stage.kind === "with") {
      const inner = buildChain(stage.program, context, `${prefix}with_${stage.index}_`,
                               true, withNames);
      withNames.set(stage.name, inner);
      if (state === null) state = inner;
      position += 1;
      continue;
    }

    ordinal += 1;
    const name = `${prefix}stage_${ordinal}`;
    const prevCte = state === null ? `${prefix}stage_0` : state.lastCte;
    const cols = state?.cols ?? new Cols();
    const live = state?.live ?? [];
    let result: StageResult;

    switch (stage.kind) {
      case "from": {
        const source = stage.source;
        if (source.program !== undefined) {
          const inner = buildChain(source.program, context, `${prefix}sub_${stage.index}_`,
                                   true, withNames);
          result = fromResolved(inner.lastCte, source.alias ?? "", inner.live, inner.cols);
        } else if (withNames.has(source.name)) {
          const inner = withNames.get(source.name) as ChainState;
          result = fromResolved(inner.lastCte, source.alias ?? source.name,
                                inner.live, inner.cols);
        } else {
          result = fromStage(stage, context.schema);
        }
        orderColumn = null;
        break;
      }
      case "join": {
        const source = stage.source;
        let right: ResolvedRight;
        if (source.program !== undefined) {
          const inner = buildChain(source.program, context, `${prefix}join_${stage.index}_`,
                                   true, withNames);
          const label = source.alias ?? "";
          right = {
            sql: label === "" ? inner.lastCte : `${inner.lastCte} AS ${ident(label)}`,
            label,
            columns: inner.live,
          };
        } else if (withNames.has(source.name)) {
          const inner = withNames.get(source.name) as ChainState;
          const label = source.alias ?? source.name;
          right = { sql: `${inner.lastCte} AS ${ident(label)}`, label, columns: inner.live };
        } else {
          right = rightForTable(stage, context.schema);
        }
        result = joinStage(stage, prevCte, cols, live, right);
        break;
      }
      case "set": {
        const inner = buildChain(stage.program, context, `${prefix}set_${stage.index}_`,
                                 true, withNames);
        const keyword = stage.op.toUpperCase();
        const left = orderColumn === null
          ? `SELECT * FROM ${prevCte}`
          : `SELECT * EXCLUDE (${ident(orderColumn)}) FROM ${prevCte}`;
        result = { sql: `${left} ${keyword} SELECT * FROM ${inner.lastCte}`, cols, live };
        orderColumn = null;
        break;
      }
      case "group by": {
        const aggregate = stages[position + 1];
        if (aggregate === undefined || aggregate.kind !== "aggregate") {
          throw stageError(stage, "group by must be immediately followed by aggregate");
        }
        result = groupUnitSql(stage, aggregate, prevCte, cols);
        orderColumn = null;
        position += 1;
        break;
      }
      case "sort": {
        const sort = stage as SortStage;
        const nextOrder = context.nextOrderName();
        result = {
          sql: sortStageSql(sort, prevCte, cols, orderColumn, nextOrder),
          cols,
          live,
        };
        orderColumn = nextOrder;
        break;
      }
      case "distinct":
      case "sql":
        result = singleStageSql(stage, prevCte, cols, live, orderColumn);
        orderColumn = null;
        break;
      case "derive":
        result = deriveStage(stage, prevCte, cols, live);
        break;
      default:
        result = singleStageSql(stage, prevCte, cols, live, orderColumn);
        break;
    }

    context.ctes.push({ name, sql: result.sql });
    state = { lastCte: name, cols: result.cols, live: result.live, orderColumn };
    position += 1;
  }

  if (state === null) {
    throw new CodegenError(
      "a program must define a relation with a from stage or a with definition");
  }
  if (nested && state.orderColumn !== null) {
    const out = `${prefix}out`;
    context.ctes.push({
      name: out,
      sql: `SELECT * EXCLUDE (${ident(state.orderColumn)}) FROM ${state.lastCte}`,
    });
    return { lastCte: out, cols: state.cols, live: state.live, orderColumn: null };
  }
  return state;
}

function orderNameFactory(program: Program, schema: Schema): () => string {
  const reservedNames = internalOrderNames(program, schema);
  const rawSqlTexts = program
    .filter((stage): stage is RawSqlStage => stage.kind === "sql")
    .map((stage) => stage.text.toLowerCase());
  let orderOrdinal = 0;
  return (): string => {
    let candidate: string;
    do {
      orderOrdinal += 1;
      candidate = `__unsequel_internal_order_${orderOrdinal}`;
    } while (reservedNames.has(candidate.toLowerCase())
      || rawSqlTexts.some((text) => text.includes(candidate.toLowerCase())));
    reservedNames.add(candidate.toLowerCase());
    return candidate;
  };
}

export function emitSql(program: Program, schema: Schema, options: EmitOptions = {}): string {
  if (program.some((stage) => stage.kind === "create" || stage.kind === "into")) {
    return emitStatements(program, schema);
  }
  const stages = truncate(program, options.stopAt);
  if (stages.length === 0) {
    throw new CodegenError("a program cannot be empty");
  }

  const nextOrderName = orderNameFactory(program, schema);
  const context: ChainContext = { schema, ctes: [], nextOrderName };
  const state = buildChain(stages, context, "", false, new Map());
  const body = context.ctes
    .map((cte) => `${cte.name} AS (\n  ${cte.sql}\n)`)
    .join(",\n");
  const finalOrder = state.orderColumn === null
    ? `SELECT * FROM ${state.lastCte};`
    : `SELECT * EXCLUDE (${ident(state.orderColumn)}) FROM ${state.lastCte} `
      + `ORDER BY ${ident(state.orderColumn)};`;
  return `WITH ${body}\n${finalOrder}`;
}

/** One write statement of a `create`/`into` program. */
export interface StatementBlock {
  index: number;
  kind: "create" | "into";
  sql: string;
}

/**
 * Statement programs (`create`/`into`) emit one complete statement per write
 * stage; each nested program becomes an inline `WITH` block. `stopAt` does not
 * apply — there are no pipeline units to truncate.
 */
export function emitStatementList(program: Program, schema: Schema): StatementBlock[] {
  const nextOrderName = orderNameFactory(program, schema);
  const blocks: StatementBlock[] = [];
  for (const stage of program) {
    if (stage.kind !== "create" && stage.kind !== "into") continue;
    const context: ChainContext = { schema, ctes: [], nextOrderName };
    const withNames = new Map<string, ChainState>();
    for (const candidate of program) {
      if (candidate.kind !== "with") continue;
      const inner = buildChain(candidate.program, context, `with_${candidate.index}_`,
                               true, withNames);
      withNames.set(candidate.name, inner);
    }
    const inner = buildChain(stage.program, context, `${stage.kind}_${stage.index}_`,
                             true, withNames);
    const body = context.ctes
      .map((cte) => `${cte.name} AS (\n  ${cte.sql}\n)`)
      .join(",\n");
    const select = `SELECT * FROM ${inner.lastCte}`;
    if (stage.kind === "create") {
      blocks.push({
        index: stage.index,
        kind: stage.kind,
        sql: `${stage.replace ? "CREATE OR REPLACE" : "CREATE"} ${stage.object.toUpperCase()} `
          + `${ident(stage.name)} AS\n`
          + `WITH ${body}\n${select};`,
      });
      schema = withCreatedTable(schema, stage.name,
        inner.live.map((column) => [column, "unknown"] as const));
    } else {
      blocks.push({
        index: stage.index,
        kind: stage.kind,
        sql: `INSERT INTO ${ident(stage.name)} BY NAME\nWITH ${body}\n${select};`,
      });
    }
  }
  return blocks;
}

function emitStatements(program: Program, schema: Schema): string {
  return emitStatementList(program, schema).map((block) => block.sql).join("\n");
}
