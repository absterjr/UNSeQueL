/**
 * Canonical, idempotent formatter for PLQ programs (plan step 8).
 *
 * Rules (grammar §7, shared with the Python formatter): lowercase stage
 * keywords, uppercase expression keywords, one stage per line, single-quoted
 * strings with escapes, `-column` for descending sort, spaces around binary
 * operators, a trailing newline. Derived names always print as
 * `name = expression`; select aliases and group keys print bare when the
 * rendered expression equals the name. `sql` payloads are re-quoted with
 * newlines escaped so a stage never spans lines. Comments are not preserved.
 */

import {
  type AggregateStage,
  type BetweenExpr,
  type BinaryExpr,
  type CaseExpr,
  type CastExpr,
  type DeriveStage,
  type DistinctStage,
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
  type SelectItemNode,
  type SelectStage,
  type SkipStage,
  type SortKey,
  type SortStage,
  type Stage,
  type TakeStage,
  type UnaryExpr,
  type WindowStage,
} from "./ast.js";
import { parseProgram } from "./parser.js";

export function formatProgram(source: string): string {
  return formatStages(parseProgram(source));
}

export function formatStages(program: Program): string {
  return `${program.map(formatStage).join("\n")}\n`;
}

export function isFormatted(source: string): boolean {
  return formatProgram(source) === source;
}

// --------------------------------------------------------------------------- //
// Expressions
// --------------------------------------------------------------------------- //

const PRECEDENCE = {
  or: 1,
  and: 2,
  not: 3,
  comparison: 4,
  additive: 5,
  multiplicative: 6,
  unary: 7,
  primary: 8,
} as const;

function precedence(expr: Expr): number {
  switch (expr.kind) {
    case "binary":
      if (expr.op === "or") return PRECEDENCE.or;
      if (expr.op === "and") return PRECEDENCE.and;
      if (["+", "-", "||"].includes(expr.op)) return PRECEDENCE.additive;
      if (["*", "/", "%"].includes(expr.op)) return PRECEDENCE.multiplicative;
      return PRECEDENCE.comparison;
    case "unary":
      return expr.op === "not" ? PRECEDENCE.not : PRECEDENCE.unary;
    case "like":
    case "in":
    case "between":
    case "is null":
      return PRECEDENCE.comparison;
    default:
      return PRECEDENCE.primary;
  }
}

function renderString(value: string): string {
  const escaped = value
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "''")
    .replace(/\n/g, "\\n");
  return `'${escaped}'`;
}

function renderLiteral(value: string | number | boolean | null): string {
  if (value === null) return "NULL";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  if (typeof value === "number") return String(value);
  return renderString(value);
}

function render(expr: Expr, minimum = 0): string {
  const text = renderInner(expr);
  return precedence(expr) < minimum ? `(${text})` : text;
}

function renderInner(expr: Expr): string {
  switch (expr.kind) {
    case "literal":
      return renderLiteral(expr.value);
    case "name":
      return expr.name;
    case "unary": {
      const unary = expr as UnaryExpr;
      if (unary.op === "not") {
        return `NOT ${render(unary.operand, PRECEDENCE.comparison)}`;
      }
      return `${unary.op}${render(unary.operand, PRECEDENCE.unary)}`;
    }
    case "binary": {
      const binary = expr as BinaryExpr;
      const operator = binary.op === "and" || binary.op === "or"
        ? binary.op.toUpperCase()
        : binary.op;
      const level = precedence(binary);
      return `${render(binary.left, level)} ${operator} ${render(binary.right, level + 1)}`;
    }
    case "like": {
      const like = expr as LikeExpr;
      const keyword = like.negated ? "NOT LIKE" : "LIKE";
      return `${render(like.value, PRECEDENCE.additive)} ${keyword} `
        + `${render(like.pattern, PRECEDENCE.additive)}`;
    }
    case "in": {
      const inList = expr as InExpr;
      const keyword = inList.negated ? "NOT IN" : "IN";
      const options = inList.options.map((option) => render(option)).join(", ");
      return `${render(inList.value, PRECEDENCE.additive)} ${keyword} (${options})`;
    }
    case "between": {
      const between = expr as BetweenExpr;
      const keyword = between.negated ? "NOT BETWEEN" : "BETWEEN";
      return `${render(between.value, PRECEDENCE.additive)} ${keyword} `
        + `${render(between.lower, PRECEDENCE.additive)} AND `
        + `${render(between.upper, PRECEDENCE.additive)}`;
    }
    case "is null": {
      const isNull = expr as IsNullExpr;
      return `${render(isNull.value, PRECEDENCE.additive)} `
        + `${isNull.negated ? "IS NOT NULL" : "IS NULL"}`;
    }
    case "case": {
      const caseExpr = expr as CaseExpr;
      const parts = ["CASE"];
      for (const branch of caseExpr.branches) {
        parts.push(`WHEN ${render(branch.when)} THEN ${render(branch.then)}`);
      }
      if (caseExpr.elseExpr !== null) parts.push(`ELSE ${render(caseExpr.elseExpr)}`);
      parts.push("END");
      return parts.join(" ");
    }
    case "cast": {
      const cast = expr as CastExpr;
      return `CAST(${render(cast.operand)} AS ${cast.typeName})`;
    }
    case "call": {
      const call = expr as FuncCallExpr;
      const name = call.name.toUpperCase();
      if (call.star) return `${name}(*)`;
      const prefix = call.distinct ? "DISTINCT " : "";
      return `${name}(${prefix}${call.args.map((arg) => render(arg)).join(", ")})`;
    }
  }
}

// --------------------------------------------------------------------------- //
// Stages
// --------------------------------------------------------------------------- //

function renderSource(source: { name: string; alias: string | null }): string {
  return source.alias === null ? source.name : `${source.name} as ${source.alias}`;
}

function renderSortKey(key: SortKey): string {
  return `${key.descending ? "-" : ""}${render(key.expr)}`;
}

function named(name: string, expr: Expr): string {
  const rendered = render(expr);
  return rendered === name ? name : `${name} = ${rendered}`;
}

function quoteSql(text: string): string {
  const escaped = text
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\n/g, "\\n");
  return `"${escaped}"`;
}

function formatStage(stage: Stage): string {
  switch (stage.kind) {
    case "from":
      return `from ${renderSource((stage as FromStage).source)}`;
    case "join": {
      const join = stage as JoinStage;
      const keyword = join.joinKind === "left" ? "left join" : "join";
      return `${keyword} ${renderSource(join.source)} on ${render(join.on)}`;
    }
    case "filter":
      return `filter ${render((stage as FilterStage).condition)}`;
    case "derive": {
      const derive = stage as DeriveStage;
      const items = derive.items.map((item) => `${item.name} = ${render(item.expr)}`);
      return `derive ${items.join(", ")}`;
    }
    case "group by": {
      const group = stage as GroupByStage;
      const keys = group.keys.map((key) => named(key.name, key.expr));
      return `group by ${keys.join(", ")}`;
    }
    case "aggregate": {
      const aggregate = stage as AggregateStage;
      const items = aggregate.items
        .map((item) => `${item.name} = ${render(item.call)}`);
      return `aggregate ${items.join(", ")}`;
    }
    case "having":
      return `having ${render((stage as HavingStage).condition)}`;
    case "window": {
      const window = stage as WindowStage;
      const call = window.call;
      const args = call.star ? "*" : call.args.map((arg) => render(arg)).join(", ");
      const parts: string[] = [];
      if (call.partitionBy.length > 0) {
        parts.push(`partition by ${call.partitionBy.map((expr) => render(expr)).join(", ")}`);
      }
      if (call.orderBy.length > 0) {
        parts.push(`order by ${call.orderBy.map(renderSortKey).join(", ")}`);
      }
      return `window ${window.name} = ${call.func.toUpperCase()}(${args}) `
        + `over (${parts.join(" ")})`;
    }
    case "select": {
      const select = stage as SelectStage;
      if (select.star) return "select *";
      const items = select.items.map((item: SelectItemNode) => {
        const rendered = render(item.expr);
        if (item.alias === null) return rendered;
        return rendered === item.alias ? rendered : `${item.alias} = ${rendered}`;
      });
      return `select ${items.join(", ")}`;
    }
    case "sort": {
      const sort = stage as SortStage;
      return `sort ${sort.keys.map(renderSortKey).join(", ")}`;
    }
    case "skip":
      return `skip ${(stage as SkipStage).count}`;
    case "take":
      return `take ${(stage as TakeStage).count}`;
    case "distinct":
      return "distinct";
    case "sql":
      return `sql ${quoteSql((stage as RawSqlStage).text)}`;
  }
}
