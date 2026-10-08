/**
 * PLQ AST contract (plan step 4, audit batch B3).
 *
 * One typed node per grammar stage (docs/grammar.md §3) plus the expression
 * nodes from §5. Every node carries its source span; stage nodes also carry
 * their 1-based grammar index. `loweringUnits` maps grammar stages to the CTE
 * units from §1.1: every stage is its own unit except `group by` +
 * `aggregate`, which share one CTE.
 */

import type { Token } from "./lexer.js";

export interface Position {
  /** 1-based line. */
  line: number;
  /** 1-based column. */
  column: number;
}

export interface Span {
  /** Inclusive start. */
  start: Position;
  /** Exclusive end (one past the last character). */
  end: Position;
}

export function positionOf(token: Token): Position {
  return { line: token.line, column: token.column };
}

export function tokenSpan(token: Token): Span {
  return {
    start: { line: token.line, column: token.column },
    end: { line: token.endLine, column: token.endColumn },
  };
}

export function spanning(first: Span, last: Span): Span {
  return { start: first.start, end: last.end };
}

// --------------------------------------------------------------------------- //
// Expressions (grammar §5)
// --------------------------------------------------------------------------- //

export interface LiteralExpr {
  kind: "literal";
  value: string | number | boolean | null;
  span: Span;
}

export interface NameExpr {
  /** Bare or qualified (dotted) name, e.g. "customer" or "members.customer". */
  kind: "name";
  name: string;
  span: Span;
}

export type BinaryOperator =
  | "+" | "-" | "*" | "/" | "%" | "||"
  | "=" | "==" | "!=" | "<>" | "<" | "<=" | ">" | ">="
  | "and" | "or";

export interface BinaryExpr {
  kind: "binary";
  op: BinaryOperator;
  left: Expr;
  right: Expr;
  span: Span;
}

export interface UnaryExpr {
  kind: "unary";
  op: "+" | "-" | "not";
  operand: Expr;
  span: Span;
}

export interface LikeExpr {
  kind: "like";
  negated: boolean;
  value: Expr;
  pattern: Expr;
  span: Span;
}

export interface InExpr {
  kind: "in";
  negated: boolean;
  value: Expr;
  options: readonly Expr[];
  span: Span;
}

export interface BetweenExpr {
  kind: "between";
  negated: boolean;
  value: Expr;
  lower: Expr;
  upper: Expr;
  span: Span;
}

export interface IsNullExpr {
  kind: "is null";
  negated: boolean;
  value: Expr;
  span: Span;
}

export interface CaseBranch {
  when: Expr;
  then: Expr;
}

export interface CaseExpr {
  kind: "case";
  branches: readonly CaseBranch[];
  elseExpr: Expr | null;
  span: Span;
}

export interface CastExpr {
  kind: "cast";
  operand: Expr;
  typeName: string;
  span: Span;
}

export interface FuncCallExpr {
  kind: "call";
  name: string;
  /** COUNT(DISTINCT expr) */
  distinct: boolean;
  /** COUNT(*) — the only wildcard call form (grammar §5). */
  star: boolean;
  args: readonly Expr[];
  span: Span;
}

export type Expr =
  | LiteralExpr
  | NameExpr
  | BinaryExpr
  | UnaryExpr
  | LikeExpr
  | InExpr
  | BetweenExpr
  | IsNullExpr
  | CaseExpr
  | CastExpr
  | FuncCallExpr;

// --------------------------------------------------------------------------- //
// Stages (grammar §3)
// --------------------------------------------------------------------------- //

export type StageKind =
  | "from"
  | "join"
  | "filter"
  | "derive"
  | "group by"
  | "aggregate"
  | "having"
  | "window"
  | "select"
  | "sort"
  | "skip"
  | "take"
  | "distinct"
  | "sql";

export interface StageBase {
  /** 1-based grammar stage ordinal (docs/grammar.md §1.1). */
  index: number;
  kind: StageKind;
  /** Canonical keyword, e.g. "join" or "group by". */
  keyword: string;
  span: Span;
}

export interface SourceRef {
  name: string;
  alias: string | null;
  span: Span;
}

export interface NamedExpr {
  name: string;
  expr: Expr;
  span: Span;
}

export interface NamedAggregate {
  name: string;
  call: FuncCallExpr;
  span: Span;
}

export interface SelectItemNode {
  expr: Expr;
  alias: string | null;
  span: Span;
}

export interface SortKey {
  expr: Expr;
  descending: boolean;
  span: Span;
}

export interface WindowCall {
  func: string;
  args: readonly Expr[];
  /** COUNT(*) OVER (...) — the only wildcard window form. */
  star: boolean;
  partitionBy: readonly Expr[];
  orderBy: readonly SortKey[];
}

export interface FromStage extends StageBase {
  kind: "from";
  source: SourceRef;
}

export interface JoinStage extends StageBase {
  kind: "join";
  joinKind: "inner" | "left";
  source: SourceRef;
  on: Expr;
}

export interface FilterStage extends StageBase {
  kind: "filter";
  condition: Expr;
}

export interface DeriveStage extends StageBase {
  kind: "derive";
  /** "row" before `group by`, "group" after `aggregate`. */
  phase: "row" | "group";
  items: readonly NamedExpr[];
}

export interface GroupByStage extends StageBase {
  kind: "group by";
  keys: readonly NamedExpr[];
}

export interface AggregateStage extends StageBase {
  kind: "aggregate";
  items: readonly NamedAggregate[];
}

export interface HavingStage extends StageBase {
  kind: "having";
  condition: Expr;
}

export interface WindowStage extends StageBase {
  kind: "window";
  name: string;
  call: WindowCall;
}

export interface SelectStage extends StageBase {
  kind: "select";
  star: boolean;
  items: readonly SelectItemNode[];
}

export interface SortStage extends StageBase {
  kind: "sort";
  keys: readonly SortKey[];
}

export interface SkipStage extends StageBase {
  kind: "skip";
  count: number;
}

export interface TakeStage extends StageBase {
  kind: "take";
  count: number;
}

export interface DistinctStage extends StageBase {
  kind: "distinct";
}

export interface RawSqlStage extends StageBase {
  kind: "sql";
  text: string;
}

export type Stage =
  | FromStage
  | JoinStage
  | FilterStage
  | DeriveStage
  | GroupByStage
  | AggregateStage
  | HavingStage
  | WindowStage
  | SelectStage
  | SortStage
  | SkipStage
  | TakeStage
  | DistinctStage
  | RawSqlStage;

export type Program = readonly Stage[];

// --------------------------------------------------------------------------- //
// Grammar stages -> lowering units / CTEs (grammar §1.1)
// --------------------------------------------------------------------------- //

/** Minimal stage shape needed for unit mapping (full stages are assignable). */
export interface StageRef {
  kind: StageKind;
  index: number;
}

export interface LoweringUnit {
  /** 1-based unit ordinal; the CTE is named `stage_${ordinal}`. */
  ordinal: number;
  cte: string;
  /** Grammar stage ordinals feeding this unit, in order. */
  stages: readonly number[];
}

export function loweringUnits(stages: readonly StageRef[]): LoweringUnit[] {
  const units: LoweringUnit[] = [];
  let position = 0;
  while (position < stages.length) {
    const current = stages[position] as StageRef;
    const next = stages[position + 1];
    const ordinal = units.length + 1;
    if (current.kind === "group by" && next?.kind === "aggregate") {
      units.push({ ordinal, cte: `stage_${ordinal}`, stages: [current.index, next.index] });
      position += 2;
      continue;
    }
    units.push({ ordinal, cte: `stage_${ordinal}`, stages: [current.index] });
    position += 1;
  }
  return units;
}

/** The unit ordinal containing the given grammar stage ordinal. */
export function unitIndexFor(stages: readonly StageRef[], stageIndex: number): number {
  for (const unit of loweringUnits(stages)) {
    if (unit.stages.includes(stageIndex)) return unit.ordinal;
  }
  throw new RangeError(`no stage ${stageIndex} in a program of ${stages.length} stages`);
}
