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

import { expressionName, type Expr, type Program, type SelectItemNode, type SourceRef, type Stage, type WindowCall } from "./ast.js";import { SemanticError } from "./errors.js";
import { type Schema, type TypeFamily, typeFamily, withCreatedTable } from "./schema.js";

export { SemanticError };

export interface StageLineage {
  index: number;
  keyword: string;
  columns: readonly string[];
}

class Scope {
  private readonly families = new Map<string, TypeFamily>();
  private readonly ambiguousNames = new Set<string>();

  known(name: string): boolean {
    return this.families.has(name);
  }

  family(name: string): TypeFamily {
    return this.families.get(name) ?? "unknown";
  }

  set(name: string, family: TypeFamily): void {
    this.families.set(name, family);
  }

  markAmbiguous(name: string): void {
    this.ambiguousNames.add(name.toLowerCase());
  }

  isAmbiguous(name: string): boolean {
    return this.ambiguousNames.has(name.toLowerCase());
  }

  entries(): Array<[string, TypeFamily]> {
    return [...this.families.entries()];
  }

  snapshot(): Map<string, TypeFamily> {
    return new Map(this.families);
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
    if (!name.includes(".") && scope.isAmbiguous(name)) {
      throw semanticError(stage,
        `${role} references ambiguous column '${name}'; qualify it with a source label`);
    }
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
      if (name === "STRFTIME") return "text";
      if (name === "DATE_TRUNC") return "temporal";
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

interface Analysis {
  lineage: StageLineage[];
  columns: string[];
  families: Map<string, TypeFamily>;
  opaque: boolean;
}

interface ResolvedSource {
  label: string;
  families: Map<string, TypeFamily>;
}

function tableFamilies(columns: ReadonlyMap<string, string>): Map<string, TypeFamily> {
  const families = new Map<string, TypeFamily>();
  for (const [column, declared] of columns) families.set(column, typeFamily(declared));
  return families;
}

/**
 * Table names a `sql` hatch text creates (lowercased -> stage index). Scans
 * nested programs too: a hatch inside `with`/`set`/`create`/`into` can still
 * tempt a later stage into referencing the table it builds.
 */
function hatchCreatedTables(program: Program): Map<string, number> {
  const hatch = new Map<string, number>();
  const visit = (stages: Program): void => {
    for (const stage of stages) {
      if (stage.kind === "sql") {
        const match = /create\s+(?:or\s+replace\s+)?table\s+["`]?([A-Za-z_][A-Za-z0-9_]*)/i
          .exec(stage.text);
        if (match !== null) hatch.set((match[1] as string).toLowerCase(), stage.index);
      } else if (stage.kind === "with" || stage.kind === "set"
                 || stage.kind === "create" || stage.kind === "into") {
        visit(stage.program);
      }
    }
  };
  visit(program);
  return hatch;
}

function hatchHint(hatchTables: ReadonlyMap<string, number>, name: string): string {
  const index = hatchTables.get(name.toLowerCase());
  if (index === undefined) return "";
  return `; the sql hatch at stage ${index} creates a table named '${name}', `
    + "but hatches cannot feed later stages in the same program; create the "
    + "table in one run and query it in the next";
}

function resolveSource(source: SourceRef, schema: Schema, ctes: Map<string, Analysis>,
                       stage: Stage, role: "from" | "join",
                       hatchTables: ReadonlyMap<string, number> = new Map()): ResolvedSource {
  if (source.program !== undefined) {
    const inner = analyzeProgram(source.program, schema, ctes, hatchTables);
    return { label: source.alias ?? "", families: inner.families };
  }
  const cte = ctes.get(source.name);
  if (cte !== undefined) {
    return { label: source.alias ?? source.name, families: cte.families };
  }
  const table = schema.tables.get(source.name);
  if (table === undefined) {
    const hint = hatchHint(hatchTables, source.name);
    throw semanticError(stage, role === "from"
      ? `unknown table '${source.name}'${hint}`
      : `unknown joined table '${source.name}'${hint}`);
  }
  return {
    label: source.alias ?? source.name.split(".").pop() ?? source.name,
    families: tableFamilies(table.columns),
  };
}

function addSourceColumns(scope: Scope, source: ResolvedSource): void {
  for (const [column, family] of source.families) {
    if (column.includes(".")) continue;
    scope.set(`${source.label}.${column}`, family);
    scope.set(column, family);
  }
}

function analyzeProgram(stages: Program, schema: Schema,
                        ctes: Map<string, Analysis>,
                        hatchTables: ReadonlyMap<string, number> = new Map()): Analysis {
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
      case "with": {
        const inner = analyzeProgram(stage.program, schema, ctes, hatchTables);
        ctes.set(stage.name, inner);
        lineage.push({ index: stage.index, keyword: stage.keyword, columns: inner.columns });
        continue;
      }
      case "from": {
        addSourceColumns(scope, resolveSource(stage.source, schema, ctes, stage, "from",
          hatchTables));
        break;
      }
      case "join": {
        const resolved = resolveSource(stage.source, schema, ctes, stage, "join", hatchTables);
        const label = resolved.label;
        const taken = new Set(scope.bareNames());
        for (const [column, family] of resolved.families) {
          if (column.includes(".")) continue;
          scope.set(`${label}.${column}`, family);
          if (scope.known(column)) scope.markAmbiguous(column);
          const exposed = taken.has(column) ? `${label}_${column}` : column;
          if (taken.has(exposed)) {
            throw semanticError(stage,
              `join exposes duplicate column '${exposed}'; alias one of the joins`);
          }
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
          const family = infer(key.expr, scope);
          groupScope.set(key.name, family);
          if (key.expr.kind === "name") {
            if (key.expr.name.includes(".")) {
              groupScope.set(key.expr.name, family);
            } else {
              for (const [name, sourceFamily] of scope.entries()) {
                if (name.includes(".")
                    && name.split(".").pop()?.toLowerCase() === key.expr.name.toLowerCase()) {
                  groupScope.set(name, sourceFamily);
                }
              }
            }
          }
        }
        groupRowScope = scope;
        scope = groupScope;
        break;
      }
      case "aggregate": {
        const rowScope = groupRowScope ?? scope;
        for (const item of stage.items) {
          checkRefs(item.call, rowScope, stage, `aggregate '${item.name}'`);
          if (scope.known(item.name)) {
            throw semanticError(stage,
              `aggregate '${item.name}' conflicts with the group key of the same name`);
          }
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
        {
          const laterAliases = new Set<string>();
          const index = stages.findIndex((candidate) => candidate === stage);
          for (const later of stages.slice(index + 1)) {
            if (later.kind === "select" && !later.star) {
              for (const item of later.items) laterAliases.add(selectName(item));
            }
          }
          for (const name of identifiers(stage.condition)) {
            if (!scope.known(name) && laterAliases.has(name)) {
              throw semanticError(stage,
                `having runs before select, so SELECT alias '${name}' is not available; `
                + "repeat the aggregate expression or define the name before having");
            }
          }
        }
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
        if (scope.known(stage.name)) {
          throw semanticError(stage,
            `window '${stage.name}' conflicts with a live column; choose another name`);
        }
        scope.set(stage.name, windowFamily(stage.call, scope));
        break;
      }
      case "select": {
        if (stage.star) {
          const fresh = new Scope();
          for (const name of scope.bareNames()) fresh.set(name, scope.family(name));
          for (const item of stage.items) {
            checkRefs(item.expr, scope, stage, "select");
            const output = selectName(item);
            if (fresh.known(output)) {
              throw semanticError(stage,
                `select output '${output}' duplicates a live column from '*'; choose a different alias`);
            }
            fresh.set(output, infer(item.expr, scope));
          }
          scope = fresh;
        } else if (!stage.star) {
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
      case "set": {
        const inner = analyzeProgram(stage.program, schema, ctes, hatchTables);
        if (!inner.opaque) {
          const left = scope.bareNames().length;
          if (left !== inner.columns.length) {
            throw semanticError(stage,
              `'${stage.op}' requires the same number of columns on both sides `
              + `(left ${left}, right ${inner.columns.length})`);
          }
        }
        break;
      }
      case "create": {
        const create = stage as { kind: "create"; name: string; program: Program };
        const inner = analyzeProgram(create.program, schema, new Map(ctes), hatchTables);
        lineage.push({ index: stage.index, keyword: stage.keyword, columns: inner.columns });
        // Later statements see this table: `create table a = (...)` then `from a`.
        if (!inner.opaque) {
          schema = withCreatedTable(schema, create.name, inner.columns.map(
            (column) => [column, inner.families.get(column) ?? "unknown"] as const));
        }
        continue;
      }
      case "into": {
        const into = stage as { kind: "into"; name: string; program: Program };
        const inner = analyzeProgram(into.program, schema, new Map(ctes), hatchTables);
        const table = schema.tables.get(into.name);
        if (table === undefined) {
          throw semanticError(stage,
            `into '${into.name}' needs an existing table; create it first `
            + "(create table ...)");
        }
        if (!inner.opaque) {
          checkIntoColumns(stage, into.name, table.columns, inner.columns, inner.families);
        }
        lineage.push({ index: stage.index, keyword: stage.keyword, columns: inner.columns });
        continue;
      }
      case "sql": {
        opaque = true;
        scope = new Scope();
        lineage.push({ index: stage.index, keyword: stage.keyword, columns: ["<raw sql>"] });
        continue;
      }
    }

    lineage.push({ index: stage.index, keyword: stage.keyword, columns: scope.bareNames() });
  }

  return {
    lineage,
    columns: scope.bareNames(),
    families: scope.snapshot(),
    opaque,
  };
}

export function analyze(stages: Program, schema: Schema): StageLineage[] {
  return analyzeProgram(stages, schema, new Map(), hatchCreatedTables(stages)).lineage;
}

/**
 * `into` appends BY NAME: the program's columns must be exactly the target's
 * columns (any order, case-insensitive) with matching type families.
 */
function checkIntoColumns(stage: Parameters<typeof semanticError>[0], target: string,
                          targetColumns: ReadonlyMap<string, string>,
                          columns: readonly string[],
                          families: ReadonlyMap<string, TypeFamily>): void {
  const wanted = new Map<string, [string, string]>();
  for (const [name, type] of targetColumns) wanted.set(name.toLowerCase(), [name, type]);
  const got = new Map(columns.map((name) => [name.toLowerCase(), name] as const));
  const extra = [...got].filter(([key]) => !wanted.has(key)).map(([, name]) => name);
  const missing = [...wanted].filter(([key]) => !got.has(key)).map(([, [name]]) => name);
  if (extra.length > 0 || missing.length > 0) {
    const parts: string[] = [];
    if (extra.length > 0) parts.push(`not in '${target}': ${extra.join(", ")}`);
    if (missing.length > 0) parts.push(`missing: ${missing.join(", ")}`);
    throw semanticError(stage, `into '${target}' matches columns by name; ${parts.join("; ")}`);
  }
  for (const [key, name] of got) {
    const [targetName, targetType] = wanted.get(key) as [string, string];
    const expected = typeFamily(targetType);
    const actual = families.get(name) ?? "unknown";
    if (expected !== "unknown" && actual !== "unknown" && expected !== actual) {
      throw semanticError(stage,
        `into '${target}': '${name}' is ${actual} but ${target}.${targetName} is `
        + `${targetType}; CAST it explicitly`);
    }
  }
}

/**
 * Base tables a program reads: from/join sources, recursively, minus names
 * that its own `with` stages define.
 */
export function referencedTables(program: Program): Set<string> {
  const tables = new Set<string>();
  const visit = (stages: Program, shadow: ReadonlySet<string>): void => {
    const names = new Set(shadow);
    for (const stage of stages) if (stage.kind === "with") names.add(stage.name);
    for (const stage of stages) {
      if (stage.kind === "from" || stage.kind === "join") {
        if (stage.source.program !== undefined) visit(stage.source.program, names);
        else if (stage.source.name !== "" && !names.has(stage.source.name)) {
          tables.add(stage.source.name);
        }
      } else if (stage.kind === "with" || stage.kind === "set"
                 || stage.kind === "create" || stage.kind === "into") {
        visit(stage.program, names);
      }
    }
  };
  visit(program, new Set());
  return tables;
}

