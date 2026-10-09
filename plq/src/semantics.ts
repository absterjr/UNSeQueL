/**
 * Schema-aware validation (plan step 5).
 *
 * `analyze` walks a parsed program stage by stage against a `Schema`,
 * tracking which columns are alive at each point, and raises a stage-located
 * `SemanticError` for:
 *
 * - an unknown source or joined table,
 * - a reference to a column that is not alive at that stage (a bare name must
 *   be live, and a qualified name must belong to the label it names),
 * - `SUM` / `AVG` over a non-numeric column,
 * - a post-`group by` reference to something that is neither a group key nor
 *   an aggregate,
 * - a `derive` that redefines a live column.
 *
 * Join columns follow grammar §3.2: the right table's columns are addressable
 * as `<label>.<column>`, and a bare name that collides with a live column is
 * exposed as `<label>_<column>`. A `sql` stage is an integrity boundary: its
 * lineage is reported as `<raw sql>` and later stages as `<opaque>` without
 * further checks. `select *` passes the scope through unchanged.
 *
 * As a side effect `analyze` returns the per-stage column lineage.
 */

import type { Expr, Program, SelectItemNode, Stage, WindowCall } from "./ast.js";
import { SemanticError } from "./errors.js";
import { type Schema, type TypeFamily, typeFamily } from "./schema.js";

export { SemanticError };

export interface StageLineage {
  index: number;
  keyword: string;
  columns: readonly string[];
}

class Scope {
  private readonly families = new Map<string, TypeFamily>();

  known(name: string): boolean {
    return this.families.has(name);
  }

  family(name: string): TypeFamily {
    return this.families.get(name) ?? "unknown";
  }

  set(name: string, family: TypeFamily): void {
    this.families.set(name, family);
  }

  bareNames(): string[] {
    return [...this.families.keys()].filter((name) => !name.includes("."));
  }
}

function semanticError(stage: Stage, message: string): SemanticError {
  return new SemanticError(message, {
    index: stage.index,
    keyword: stage.keyword,
    line: stage.span.start.line,
    column: stage.span.start.column,
  });
}

function checkRefs(expr: Expr, scope: Scope, stage: Stage, role: string): void {
  for (const name of identifiers(expr)) {
    if (!scope.known(name)) {
      throw semanticError(stage, `${role} references unknown column '${name}'`);
    }
  }
}

function identifiers(expr: Expr, out: Set<string> = new Set()): Set<string> {
  switch (expr.kind) {
    case "name":
      out.add(expr.name);
      break;
    case "literal":
      break;
    case "binary":
      identifiers(expr.left, out);
      identifiers(expr.right, out);
      break;
    case "unary":
      identifiers(expr.operand, out);
      break;
    case "like":
      identifiers(expr.value, out);
      identifiers(expr.pattern, out);
      break;
    case "in":
      identifiers(expr.value, out);
      for (const option of expr.options) identifiers(option, out);
      break;
    case "between":
      identifiers(expr.value, out);
      identifiers(expr.lower, out);
      identifiers(expr.upper, out);
      break;
    case "is null":
      identifiers(expr.value, out);
      break;
    case "case":
      for (const branch of expr.branches) {
        identifiers(branch.when, out);
        identifiers(branch.then, out);
      }
      if (expr.elseExpr !== null) identifiers(expr.elseExpr, out);
      break;
    case "cast":
      identifiers(expr.operand, out);
      break;
    case "call":
      for (const arg of expr.args) identifiers(arg, out);
      break;
  }
  return out;
}

/** Output name for a select item without an alias, mirroring the engines. */
function expressionName(expr: Expr): string {
  switch (expr.kind) {
    case "name":
      return expr.name.split(".").pop() ?? expr.name;
    case "call":
      return expr.name.toLowerCase();
    case "case":
      return "case";
    case "cast":
      return "cast";
    case "in":
      return "in";
    case "between":
      return "between";
    default:
      return "expression";
  }
}

function selectName(item: SelectItemNode): string {
  return item.alias ?? expressionName(item.expr);
}

function infer(expr: Expr, scope: Scope): TypeFamily {
  switch (expr.kind) {
    case "literal":
      if (typeof expr.value === "boolean") return "boolean";
      if (typeof expr.value === "number") return "numeric";
      if (typeof expr.value === "string") return "text";
      return "unknown";
    case "name":
      return scope.family(expr.name);
    case "unary":
      return expr.op === "not" ? "boolean" : "numeric";
    case "binary":
      if (["+", "-", "*", "/", "%"].includes(expr.op)) return "numeric";
      if (expr.op === "||") return "text";
      return "boolean";
    case "like":
    case "in":
    case "between":
    case "is null":
      return "boolean";
    case "call": {
      const name = expr.name.toUpperCase();
      if (["SUM", "AVG", "COUNT", "ABS", "ROUND", "LENGTH"].includes(name)) return "numeric";
      if (["MIN", "MAX", "COALESCE"].includes(name) && expr.args.length > 0) {
        return infer(expr.args[0] as Expr, scope);
      }
      if (["LOWER", "UPPER", "TRIM", "CONCAT"].includes(name)) return "text";
      return "unknown";
    }
    case "case": {
      const families = new Set<TypeFamily>();
      for (const branch of expr.branches) families.add(infer(branch.then, scope));
      if (expr.elseExpr !== null) families.add(infer(expr.elseExpr, scope));
      families.delete("unknown");
      if (families.size !== 1) return "unknown";
      return [...families][0] as TypeFamily;
    }
    case "cast":
      return typeFamily(expr.typeName);
  }
}

function windowFamily(call: WindowCall, scope: Scope): TypeFamily {
  const name = call.func.toUpperCase();
  if (["SUM", "AVG", "COUNT", "ROW_NUMBER", "RANK", "DENSE_RANK"].includes(name)) {
    return "numeric";
  }
  if (["MIN", "MAX", "LAG", "LEAD"].includes(name) && call.args.length > 0) {
    return infer(call.args[0] as Expr, scope);
  }
  return "unknown";
}

export function analyze(stages: Program, schema: Schema): StageLineage[] {
  let scope = new Scope();
  let opaque = false;
  let groupRowScope: Scope | null = null;
  const lineage: StageLineage[] = [];

  for (const stage of stages) {
    if (opaque) {
      lineage.push({ index: stage.index, keyword: stage.keyword, columns: ["<opaque>"] });
      continue;
    }

    switch (stage.kind) {
      case "from": {
        const table = schema.tables.get(stage.source.name);
        if (table === undefined) {
          throw semanticError(stage, `unknown table '${stage.source.name}'`);
        }
        const label = stage.source.alias
          ?? stage.source.name.split(".").pop()
          ?? stage.source.name;
        for (const [column, declared] of table.columns) {
          const family = typeFamily(declared);
          scope.set(column, family);
          scope.set(`${label}.${column}`, family);
        }
        break;
      }
      case "join": {
        const table = schema.tables.get(stage.source.name);
        if (table === undefined) {
          throw semanticError(stage, `unknown joined table '${stage.source.name}'`);
        }
        const label = stage.source.alias
          ?? stage.source.name.split(".").pop()
          ?? stage.source.name;
        const taken = new Set(scope.bareNames());
        for (const [column, declared] of table.columns) {
          const family = typeFamily(declared);
          scope.set(`${label}.${column}`, family);
          const exposed = taken.has(column) ? `${label}_${column}` : column;
          scope.set(exposed, family);
          taken.add(exposed);
        }
        checkRefs(stage.on, scope, stage, "join condition");
        break;
      }
      case "filter":
        checkRefs(stage.condition, scope, stage, "filter condition");
        break;
      case "derive":
        for (const item of stage.items) {
          checkRefs(item.expr, scope, stage, `derive '${item.name}'`);
          if (scope.known(item.name)) {
            throw semanticError(stage,
              `derive '${item.name}' conflicts with a live column; choose another name`);
          }
          scope.set(item.name, infer(item.expr, scope));
        }
        break;
      case "group by": {
        const groupScope = new Scope();
        for (const key of stage.keys) {
          checkRefs(key.expr, scope, stage, `group key '${key.name}'`);
          groupScope.set(key.name, infer(key.expr, scope));
        }
        groupRowScope = scope;
        scope = groupScope;
        break;
      }
      case "aggregate": {
        const rowScope = groupRowScope ?? scope;
        for (const item of stage.items) {
          checkRefs(item.call, rowScope, stage, `aggregate '${item.name}'`);
          const fname = item.call.name.toUpperCase();
          if ((fname === "SUM" || fname === "AVG") && item.call.args.length > 0) {
            const argFamily = infer(item.call.args[0] as Expr, rowScope);
            if (argFamily !== "numeric" && argFamily !== "unknown") {
              throw semanticError(stage,
                `${fname} over '${item.name}' needs a numeric column, got ${argFamily}`);
            }
          }
          scope.set(item.name, infer(item.call, rowScope));
        }
        groupRowScope = null;
        break;
      }
      case "having":
        checkRefs(stage.condition, scope, stage, "having condition");
        break;
      case "window": {
        for (const arg of stage.call.args) {
          checkRefs(arg, scope, stage, `window '${stage.name}'`);
        }
        for (const expr of stage.call.partitionBy) {
          checkRefs(expr, scope, stage, `window '${stage.name}'`);
        }
        for (const key of stage.call.orderBy) {
          checkRefs(key.expr, scope, stage, `window '${stage.name}'`);
        }
        scope.set(stage.name, windowFamily(stage.call, scope));
        break;
      }
      case "select": {
        if (!stage.star) {
          const fresh = new Scope();
          for (const item of stage.items) {
            checkRefs(item.expr, scope, stage, "select");
            fresh.set(selectName(item), infer(item.expr, scope));
          }
          scope = fresh;
        }
        break;
      }
      case "sort":
        for (const key of stage.keys) {
          checkRefs(key.expr, scope, stage, "sort key");
        }
        break;
      case "skip":
      case "take":
      case "distinct":
        break;
      case "sql": {
        opaque = true;
        scope = new Scope();
        lineage.push({ index: stage.index, keyword: stage.keyword, columns: ["<raw sql>"] });
        continue;
      }
    }

    lineage.push({ index: stage.index, keyword: stage.keyword, columns: scope.bareNames() });
  }

  return lineage;
}
