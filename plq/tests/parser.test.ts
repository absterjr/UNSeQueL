import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type {
  AggregateStage,
  DeriveStage,
  FilterStage,
  FromStage,
  GroupByStage,
  JoinStage,
  NameExpr,
  SelectStage,
  SortStage,
  WindowStage,
} from "../src/ast.js";
import { ParseError, PipelineError } from "../src/errors.js";
import { parseProgram } from "../src/parser.js";
import { readdirSync } from "node:fs";

/**
 * Step 4: the parser. Done-when from the plan: all reference queries parse
 * into correct trees, and broken queries produce specific errors pointing at
 * the exact stage and location.
 */

const REFERENCE = join(__dirname, "..", "examples", "reference");
const COVERAGE = join(__dirname, "..", "examples", "coverage");

function parse(source: string) {
  return parseProgram(source);
}

function filterCondition(source: string) {
  const program = parse(`from orders\nfilter ${source}`);
  return (program[1] as FilterStage).condition;
}

function expectPipelineError(source: string): PipelineError {
  try {
    parse(source);
  } catch (error) {
    expect(error).toBeInstanceOf(PipelineError);
    return error as PipelineError;
  }
  throw new Error(`expected a PipelineError for: ${source}`);
}

describe("parser: reference corpus", () => {
  const files = readdirSync(REFERENCE).filter((f) => f.endsWith(".plq")).sort();

  it("parses all 21 reference queries into programs", () => {
    expect(files).toHaveLength(21);
    for (const file of files) {
      const program = parse(readFileSync(join(REFERENCE, file), "utf8"));
      expect(program.length, file).toBeGreaterThan(0);
      expect(program[0]?.kind, file).toBe("from");
      program.forEach((stage, i) => expect(stage.index, file).toBe(i + 1));
    }
  });

  it("q20 parses to the expected stage sequence with source lines", () => {
    const program = parse(readFileSync(join(REFERENCE, "q20_full_pipeline.plq"), "utf8"));
    expect(program.map((stage) => stage.kind)).toEqual([
      "from", "join", "derive", "group by", "aggregate", "having", "derive", "sort", "take",
    ]);
    expect(program.map((stage) => stage.span.start.line)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("q09 builds group, aggregate, and descending sort nodes", () => {
    const program = parse(readFileSync(join(REFERENCE, "q09_group_count.plq"), "utf8"));
    const group = program[1] as GroupByStage;
    expect(group.keys.map((key) => key.name)).toEqual(["country"]);
    const aggregate = program[2] as AggregateStage;
    expect(aggregate.items.map((item) => item.name)).toEqual(["orders"]);
    expect(aggregate.items[0]?.call).toMatchObject({ name: "COUNT", star: true });
    const sort = program[3] as SortStage;
    expect(sort.keys.map((key) => key.descending)).toEqual([true]);
  });

  it("q21 carries the raw SQL text and a row-phase filter", () => {
    const program = parse(readFileSync(join(REFERENCE, "q21_sql_hatch.plq"), "utf8"));
    const sql = program[1];
    expect(sql?.kind).toBe("sql");
    expect((sql as { text: string }).text).toContain("ROW_NUMBER() OVER");
    const filter = program[2] as FilterStage;
    expect(filter.kind).toBe("filter");
  });

  it("q02 selects the named columns", () => {
    const program = parse(readFileSync(join(REFERENCE, "q02_project_columns.plq"), "utf8"));
    const select = program[1] as SelectStage;
    expect(select.star).toBe(false);
    expect(select.items.map((item) => (item.expr as NameExpr).name))
      .toEqual(["order_id", "customer", "quantity"]);
  });
});

describe("parser: coverage corpus", () => {
  const files = readdirSync(COVERAGE).filter((f) => f.endsWith(".plq")).sort();

  it("parses all 13 coverage fixtures", () => {
    for (const file of files) {
      const program = parse(readFileSync(join(COVERAGE, file), "utf8"));
      expect(program[0]?.kind, file).toBe("from");
    }
  });

  it("c01 keeps source aliases and qualified names", () => {
    const program = parse(readFileSync(join(COVERAGE, "c01_aliases.plq"), "utf8"));
    const from = program[0] as FromStage;
    expect(from.source).toMatchObject({ name: "analytics.orders", alias: "o" });
    const joinStage = program[1] as JoinStage;
    expect(joinStage).toMatchObject({ joinKind: "inner" });
    expect(joinStage.source).toMatchObject({ name: "products", alias: "p" });
  });

  it("c02 builds all aggregate forms", () => {
    const program = parse(readFileSync(join(COVERAGE, "c02_group_expr.plq"), "utf8"));
    const group = program[1] as GroupByStage;
    expect(group.keys.map((key) => key.name)).toEqual(["double_qty"]);
    const aggregate = program[2] as AggregateStage;
    expect(aggregate.items.map((item) => item.name))
      .toEqual(["customers", "avg_qty", "min_qty", "max_qty", "count"]);
    expect(aggregate.items[0]?.call).toMatchObject({ name: "COUNT", distinct: true });
    expect(aggregate.items[4]?.call).toMatchObject({ name: "COUNT", star: true });
  });

  it("c12 builds three window stages with partition and order clauses", () => {
    const program = parse(readFileSync(join(COVERAGE, "c12_window.plq"), "utf8"));
    const windows = program.filter((stage) => stage.kind === "window") as WindowStage[];
    expect(windows.map((stage) => stage.name))
      .toEqual(["rn", "customer_total", "customer_count"]);
    expect(windows[0]?.call.partitionBy).toHaveLength(1);
    expect(windows[0]?.call.orderBy.map((key) => key.descending)).toEqual([true]);
    expect(windows[2]?.call).toMatchObject({ func: "COUNT", star: true });
  });

  it("c05 parses every derive item", () => {
    const program = parse(readFileSync(join(COVERAGE, "c05_derive_multi.plq"), "utf8"));
    const derive = program[1] as DeriveStage;
    expect(derive.phase).toBe("row");
    expect(derive.items).toHaveLength(11);
    expect(derive.items[0]?.name).toBe("gross");
  });
});

describe("parser: expressions", () => {
  it("applies arithmetic and comparison precedence", () => {
    const condition = filterCondition("1 + 2 * 3 = 7");
    expect(condition).toMatchObject({ kind: "binary", op: "=" });
    const left = condition.kind === "binary" ? condition.left : null;
    expect(left).toMatchObject({ kind: "binary", op: "+" });
    const right = left && left.kind === "binary" ? left.right : null;
    expect(right).toMatchObject({ kind: "binary", op: "*" });
  });

  it("binds BETWEEN's AND internally", () => {
    const condition = filterCondition("a BETWEEN 1 AND 2 AND b = 3");
    expect(condition).toMatchObject({ kind: "binary", op: "and" });
    const left = condition.kind === "binary" ? condition.left : null;
    expect(left).toMatchObject({ kind: "between", negated: false });
  });

  it("normalizes prefix NOT and infix NOT IN", () => {
    const prefix = filterCondition("not a in (1)");
    expect(prefix).toMatchObject({ kind: "unary", op: "not" });
    const operand = prefix.kind === "unary" ? prefix.operand : null;
    expect(operand).toMatchObject({ kind: "in", negated: false });

    const infix = filterCondition("a not in (1)");
    expect(infix).toMatchObject({ kind: "in", negated: true });
  });

  it("respects parentheses over precedence", () => {
    const condition = filterCondition("(a OR b) AND c");
    expect(condition).toMatchObject({ kind: "binary", op: "and" });
    const left = condition.kind === "binary" ? condition.left : null;
    expect(left).toMatchObject({ kind: "binary", op: "or" });
  });

  it("parses select x = y as an alias, not equality", () => {
    const program = parse("from orders\nselect x = y");
    const select = program[1] as SelectStage;
    expect(select.items[0]).toMatchObject({ alias: "x" });
    expect(select.items[0]?.expr).toMatchObject({ kind: "name", name: "y" });
  });

  it("parses CASE with and without ELSE", () => {
    const withElse = parse(
      "from orders\nselect CASE WHEN a > 1 THEN 'x' ELSE 'y' END");
    const item = (withElse[1] as SelectStage).items[0];
    expect(item?.expr).toMatchObject({ kind: "case" });
    const caseExpr = item?.expr;
    if (caseExpr?.kind === "case") {
      expect(caseExpr.branches).toHaveLength(1);
      expect(caseExpr.elseExpr).toMatchObject({ kind: "literal", value: "y" });
    }
    const withoutElse = parse("from orders\nselect CASE WHEN a > 1 THEN 'x' END");
    const noElse = (withoutElse[1] as SelectStage).items[0]?.expr;
    if (noElse?.kind === "case") {
      expect(noElse.elseExpr).toBeNull();
    }
  });

  it("parses CAST and == equality", () => {
    const cast = parse("from orders\nselect CAST(a AS TEXT)");
    const item = (cast[1] as SelectStage).items[0];
    expect(item?.expr).toMatchObject({ kind: "cast", typeName: "TEXT" });

    expect(filterCondition("a == b")).toMatchObject({ kind: "binary", op: "==" });
  });

  it("rejects chained comparisons with a located error", () => {
    const error = expectPipelineError("from orders\nfilter a < b < c");
    expect(error.stageIndex).toBe(2);
    expect(error.stageKeyword).toBe("filter");
    expect(error.message).toContain("chained comparisons");
  });
});

describe("parser: ordering rules", () => {
  it("rejects a pipeline that does not start with from", () => {
    const error = expectPipelineError("select country\nfrom orders");
    expect(error.stageIndex).toBe(1);
    expect(error.message).toContain("must start with 'from'");
  });

  it("rejects group by without a following aggregate", () => {
    const error = expectPipelineError("from orders\ngroup by country\nselect country");
    expect(error.stageIndex).toBe(2);
    expect(error.stageKeyword).toBe("group by");
    expect(error.message).toContain("immediately followed by aggregate");
  });

  it("rejects aggregate without group by", () => {
    const error = expectPipelineError("from orders\naggregate n = COUNT(*)");
    expect(error.stageKeyword).toBe("aggregate");
    expect(error.message).toContain("requires a preceding group by");
  });

  it("rejects having without aggregate", () => {
    const error = expectPipelineError("from orders\nhaving n > 1");
    expect(error.stageKeyword).toBe("having");
    expect(error.message).toContain("requires a preceding aggregate");
  });

  it("rejects filter after group by", () => {
    const error = expectPipelineError(
      "from orders\ngroup by country\naggregate n = COUNT(*)\nfilter quantity > 1");
    expect(error.stageIndex).toBe(4);
    expect(error.message).toContain("after group by; use having");
  });

  it("rejects join after group by", () => {
    const error = expectPipelineError(
      "from orders\ngroup by country\naggregate n = COUNT(*)\njoin members on a = members.customer");
    expect(error.stageKeyword).toBe("join");
    expect(error.message).toContain("after group by");
  });

  it("rejects stages after the sort/skip/take tail", () => {
    const cases: Array<[string, string, number]> = [
      ["from orders\ntake 5\nfilter quantity > 1", "filter", 3],
      ["from orders\nsort order_id\ntake 2\nsort -quantity", "sort", 4],
      ["from orders\nselect country\nsort country\ndistinct", "distinct", 4],
    ];
    for (const [source, keyword, index] of cases) {
      const error = expectPipelineError(source);
      expect(error.stageIndex, source).toBe(index);
      expect(error.stageKeyword, source).toBe(keyword);
    }
  });

  it("rejects sort after skip and skip after take", () => {
    expect(expectPipelineError("from orders\nskip 2\nsort order_id").message)
      .toContain("before skip/take");
    expect(expectPipelineError("from orders\ntake 2\nskip 1").message)
      .toContain("before take");
  });

  it("rejects filter after select", () => {
    const error = expectPipelineError("from orders\nselect country\nfilter quantity > 1");
    expect(error.stageIndex).toBe(3);
    expect(error.message).toContain("after select");
  });

  it("applies the same ordering rules to left join", () => {
    expect(expectPipelineError(
      "from orders\ngroup by country\naggregate n = COUNT(*)\n"
      + "left join members on a = members.customer",
    ).message).toContain("after group by");
    expect(expectPipelineError(
      "from orders\ntake 2\nleft join members on a = members.customer",
    ).message).toContain("after sort/skip/take");
  });

  it("locates empty stage bodies at the stage line", () => {
    for (const text of ["from orders\nfilter", "from orders\nselect", "from orders\ntake"]) {
      try {
        parse(text);
        throw new Error("expected an error");
      } catch (error) {
        expect(error).toBeInstanceOf(PipelineError);
        expect((error as PipelineError).line, text).toBe(2);
      }
    }
  });

  it("rejects right join with a directed message", () => {
    try {
      parse("from orders\nright join members on a = members.customer");
      throw new Error("expected an error");
    } catch (error) {
      expect(error).toBeInstanceOf(ParseError);
      expect((error as ParseError).message).toContain("not available in the pipeline grammar");
    }
  });

  it("rejects newlines inside parentheses", () => {
    try {
      parse("from orders\nfilter (a\n AND b)");
      throw new Error("expected an error");
    } catch (error) {
      expect(error).toBeInstanceOf(ParseError);
      expect((error as ParseError).message).toContain("cannot span lines");
    }
  });
});

describe("parser: aggregates and the escape hatch", () => {
  it("rejects aggregate calls outside an aggregate stage", () => {
    expect(expectPipelineError("from orders\nfilter SUM(quantity) > 1").message)
      .toContain("only valid inside an aggregate");
    expect(expectPipelineError("from orders\nselect COUNT(*)").message)
      .toContain("only valid inside an aggregate");
  });

  it("rejects bad aggregate forms", () => {
    const base = "from orders\ngroup by country\naggregate ";
    expect(expectPipelineError(`${base}n = SUM(*)`).message).toContain("only COUNT accepts");
    expect(expectPipelineError(`${base}n = SUM(SUM(quantity))`).message)
      .toContain("cannot contain aggregate calls");
    expect(expectPipelineError(`${base}SUM(quantity)`).message)
      .toContain("must be named");
    expect(expectPipelineError(`${base}n = COUNT(DISTINCT *)`).message)
      .toContain("COUNT(DISTINCT *)");
  });

  it("validates the sql stage text", () => {
    expect(expectPipelineError('from orders\nsql "UPDATE x SET y = 1"').message)
      .toContain("start it with SELECT or WITH");
    expect(expectPipelineError('from orders\nsql "SELECT 1; SELECT 2"').message)
      .toContain("';' is not allowed");
    expect(expectPipelineError('from orders\nselect country\nsql "SELECT 1"').message)
      .toContain("after select");
  });

  it("accepts WITH queries in the hatch", () => {
    const program = parse('from orders\nsql "WITH x AS (SELECT 1 AS a) SELECT a FROM x"');
    expect(program[1]?.kind).toBe("sql");
  });
});

describe("parser: window stage", () => {
  it("rejects unknown window functions", () => {
    const error = expectPipelineError(
      "from orders\nwindow rn = FOO() over (partition by customer)");
    expect(error.stageKeyword).toBe("window");
    expect(error.message).toContain("not a window function");
  });

  it("requires an over clause with at least one sub-clause", () => {
    expect(expectPipelineError("from orders\nwindow rn = ROW_NUMBER()").message)
      .toContain("expected 'over'");
    expect(expectPipelineError("from orders\nwindow rn = ROW_NUMBER() over ()").message)
      .toContain("at least one of");
  });

  it("rejects window functions outside a window stage", () => {
    const error = expectPipelineError(
      "from orders\nfilter ROW_NUMBER() over (partition by customer) = 1");
    expect(error.message).toContain("only valid in a window stage");
  });
});

describe("parser: select star and integers", () => {
  it("rejects '*' combined with other items", () => {
    expect(expectPipelineError("from orders\nselect *, order_id").message)
      .toContain("must be the only select item");
  });

  it("requires integer counts for skip/take", () => {
    expect(expectPipelineError("from orders\ntake 12.5").message)
      .toContain("non-negative integer");
    expect(expectPipelineError("from orders\nskip -1").message)
      .toContain("non-negative integer");
  });
});
