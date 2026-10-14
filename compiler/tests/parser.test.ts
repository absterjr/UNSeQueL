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
  const files = readdirSync(REFERENCE).filter((f) => f.endsWith(".unsequel")).sort();

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
    const program = parse(readFileSync(join(REFERENCE, "q20_full_pipeline.unsequel"), "utf8"));
    expect(program.map((stage) => stage.kind)).toEqual([
      "from", "join", "derive", "group by", "aggregate", "having", "derive", "sort", "take",
    ]);
    expect(program.map((stage) => stage.span.start.line)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("q09 builds group, aggregate, and descending sort nodes", () => {
    const program = parse(readFileSync(join(REFERENCE, "q09_group_count.unsequel"), "utf8"));
    const group = program[1] as GroupByStage;
    expect(group.keys.map((key) => key.name)).toEqual(["country"]);
    const aggregate = program[2] as AggregateStage;
    expect(aggregate.items.map((item) => item.name)).toEqual(["orders"]);
    expect(aggregate.items[0]?.call).toMatchObject({ name: "COUNT", star: true });
    const sort = program[3] as SortStage;
    expect(sort.keys.map((key) => key.descending)).toEqual([true]);
  });

  it("q21 carries the raw SQL text and a row-phase filter", () => {
    const program = parse(readFileSync(join(REFERENCE, "q21_sql_hatch.unsequel"), "utf8"));
    const sql = program[1];
    expect(sql?.kind).toBe("sql");
    expect((sql as { text: string }).text).toContain("ROW_NUMBER() OVER");
    const filter = program[2] as FilterStage;
    expect(filter.kind).toBe("filter");
  });

  it("q02 selects the named columns", () => {
    const program = parse(readFileSync(join(REFERENCE, "q02_project_columns.unsequel"), "utf8"));
    const select = program[1] as SelectStage;
    expect(select.star).toBe(false);
    expect(select.items.map((item) => (item.expr as NameExpr).name))
      .toEqual(["order_id", "customer", "quantity"]);
  });
});

describe("parser: coverage corpus", () => {
  const files = readdirSync(COVERAGE).filter((f) => f.endsWith(".unsequel")).sort();

  it("parses all 16 coverage fixtures", () => {
    for (const file of files) {
      const program = parse(readFileSync(join(COVERAGE, file), "utf8"));
      expect(["from", "with"], file).toContain(program[0]?.kind);
    }
  });

  it("c01 keeps source aliases and qualified names", () => {
    const program = parse(readFileSync(join(COVERAGE, "c01_aliases.unsequel"), "utf8"));
    const from = program[0] as FromStage;
    expect(from.source).toMatchObject({ name: "analytics.orders", alias: "o" });
    const joinStage = program[1] as JoinStage;
    expect(joinStage).toMatchObject({ joinKind: "inner" });
    expect(joinStage.source).toMatchObject({ name: "products", alias: "p" });
  });

  it("c02 builds all aggregate forms", () => {
    const program = parse(readFileSync(join(COVERAGE, "c02_group_expr.unsequel"), "utf8"));
    const group = program[1] as GroupByStage;
    expect(group.keys.map((key) => key.name)).toEqual(["double_qty"]);
    const aggregate = program[2] as AggregateStage;
    expect(aggregate.items.map((item) => item.name))
      .toEqual(["customers", "avg_qty", "min_qty", "max_qty", "count"]);
    expect(aggregate.items[0]?.call).toMatchObject({ name: "COUNT", distinct: true });
    expect(aggregate.items[4]?.call).toMatchObject({ name: "COUNT", star: true });
  });

  it("c12 builds three window stages with partition and order clauses", () => {
    const program = parse(readFileSync(join(COVERAGE, "c12_window.unsequel"), "utf8"));
    const windows = program.filter((stage) => stage.kind === "window") as WindowStage[];
    expect(windows.map((stage) => stage.name))
      .toEqual(["rn", "customer_total", "customer_count"]);
    expect(windows[0]?.call.partitionBy).toHaveLength(1);
    expect(windows[0]?.call.orderBy.map((key) => key.descending)).toEqual([true]);
    expect(windows[2]?.call).toMatchObject({ func: "COUNT", star: true });
  });

  it("c05 parses every derive item", () => {
    const program = parse(readFileSync(join(COVERAGE, "c05_derive_multi.unsequel"), "utf8"));
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

  it("parses quoted table and column names that are reserved or not bare identifiers", () => {
    const program = parse("from `order`\nselect `Order Date`, `2024_sales`, `group`");
    expect(program[0]).toMatchObject({
      kind: "from", source: { name: "order" },
    });
    const select = program[1] as SelectStage;
    expect(select.items.map((item) => (item.expr as NameExpr).name))
      .toEqual(["Order Date", "2024_sales", "group"]);
  });

  it("rejects chained comparisons with a located error", () => {
    const error = expectPipelineError("from orders\nfilter a < b < c");
    expect(error.stageIndex).toBe(2);
    expect(error.stageKeyword).toBe("filter");
    expect(error.message).toContain("chained comparisons");
  });
});

describe("parser: ordering rules", () => {
  it("rejects a pipeline that does not start with from or with", () => {
    const error = expectPipelineError("select country\nfrom orders");
    expect(error.stageIndex).toBe(1);
    expect(error.message).toContain("must start with 'with' or 'from'");
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

  it("allows joins, filters, derives, and projections after aggregation", () => {
    const program = parse(
      "from orders\ngroup by customer\naggregate n = COUNT(*)\n"
      + "join customers as c on customer = c.customer\n"
      + "filter n > 1\nderive doubled = n * 2\nselect customer, c.segment, doubled");
    expect(program.map((stage) => stage.kind)).toEqual([
      "from", "group by", "aggregate", "join", "filter", "derive", "select",
    ]);
  });

  it("allows repeated group/aggregate units and projections", () => {
    const program = parse(
      "from orders\ngroup by customer\naggregate total = SUM(unit_price)\n"
      + "group by band = CASE WHEN total > 100 THEN 'high' ELSE 'low' END\n"
      + "aggregate avg_total = AVG(total)\nselect band, avg_total\n"
      + "select band, avg_total");
    expect(program.map((stage) => stage.kind)).toEqual([
      "from", "group by", "aggregate", "group by", "aggregate", "select", "select",
    ]);
  });

  it("allows relational stages after sort, skip, take, and select", () => {
    const program = parse(
      "from orders\nsort order_id\ntake 5\nfilter order_id > 1\n"
      + "join products as p on product_id = p.product_id\n"
      + "derive label = p.product_name\nselect order_id, label\n"
      + "skip 1\nsort -order_id");
    expect(program.map((stage) => stage.kind)).toEqual([
      "from", "sort", "take", "filter", "join", "derive", "select", "skip", "sort",
    ]);
  });

  it("allows left joins after aggregation and pagination", () => {
    const afterGroup = parse(
      "from orders\ngroup by customer\naggregate n = COUNT(*)\n"
      + "left join members on customer = members.customer");
    expect(afterGroup.at(-1)?.kind).toBe("join");
    const afterTake = parse(
      "from orders\ntake 2\nleft join members on customer = members.customer");
    expect(afterTake.at(-1)?.kind).toBe("join");
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

  it("parses inner, outer, and cross joins", () => {
    for (const [source, kind] of [
      ["join members on orders.customer = members.customer", "inner"],
      ["left join members on orders.customer = members.customer", "left"],
      ["right join members on orders.customer = members.customer", "right"],
      ["full join members on orders.customer = members.customer", "full"],
      ["cross join members", "cross"],
    ] as const) {
      const program = parse(`from orders\n${source}`);
      expect(program[1]).toMatchObject({ kind: "join", joinKind: kind });
    }
  });

  it("allows nested programs to span lines inside parentheses", () => {
    const program = parse(
      "from (from orders\nfilter quantity > 1\ntake 2) as big");
    expect(program[0]).toMatchObject({ kind: "from" });
    const source = (program[0] as FromStage).source;
    expect(source.program?.map((stage) => stage.kind)).toEqual(["from", "filter", "take"]);
  });

  it("rejects a subquery source without an alias", () => {
    const error = expectPipelineError("from (from orders take 1)");
    expect(error.message).toContain("requires an alias");
  });

  it("parses with definitions and set-operator stages", () => {
    const program = parse(
      "with top = (from orders | take 3)\n"
      + "from top\n"
      + "union all (from archives | take 1)");
    expect(program.map((stage) => stage.kind)).toEqual(["with", "from", "set"]);
    expect(program[0]).toMatchObject({ kind: "with", name: "top" });
    expect(program[2]).toMatchObject({ kind: "set", op: "union all" });
  });

  it("rejects with definitions after from and duplicate names", () => {
    expect(expectPipelineError("from orders\nwith x = (from orders | take 1)").message)
      .toContain("before 'from'");
    expect(expectPipelineError(
      "with x = (from orders | take 1)\nwith x = (from orders | take 2)\nfrom x").message)
      .toContain("defined more than once");
  });

  it("rejects set operators before from", () => {
    const error = expectPipelineError("union (from orders take 1)");
    expect(error.message).toContain("must start with 'with' or 'from'");
  });

  it("rejects joins after a sql stage", () => {
    const error = expectPipelineError(
      'from orders\nsql "SELECT * FROM __input__"\njoin members on customer = members.customer',
    );
    expect(error.message).toContain("after a sql stage");
  });
});

describe("parser: duplicate declarations", () => {
  it("rejects duplicate select outputs", () => {
    expect(expectPipelineError("from orders\nselect quantity, quantity = 1").message)
      .toContain("more than once");
  });

  it("rejects duplicate derive names", () => {
    expect(expectPipelineError("from orders\nderive a = quantity, a = quantity * 2").message)
      .toContain("more than once");
  });

  it("rejects duplicate group keys and aggregate names", () => {
    expect(expectPipelineError(
      "from orders\ngroup by country, country\naggregate n = COUNT(*)").message)
      .toContain("more than once");
    expect(expectPipelineError(
      "from orders\ngroup by country\naggregate n = COUNT(*), n = SUM(quantity)").message)
      .toContain("more than once");
  });
});

describe("parser: functions and the hatch", () => {
  it("rejects unknown function names", () => {
    expect(expectPipelineError("from orders\nderive x = FOO(quantity)").message)
      .toContain("unknown function 'FOO'");
  });

  it("accepts the documented scalar functions", () => {
    const program = parse(
      "from orders\nderive x = COALESCE(customer, 'n/a'), y = ROUND(quantity, 2)");
    expect(program).toHaveLength(2);
  });

  it("accepts leading comments before the sql query keyword", () => {
    const line = parse('from orders\nsql "-- lead\\nSELECT order_id FROM __input__"');
    expect(line[1]?.kind).toBe("sql");
    const block = parse('from orders\nsql "/* lead */ SELECT order_id FROM __input__"');
    expect(block[1]?.kind).toBe("sql");
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
    const afterProjection = parse('from orders\nselect country\nsql "SELECT 1"');
    expect(afterProjection.at(-1)?.kind).toBe("sql");
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

  it("requires an over clause and permits a whole-relation window", () => {
    expect(expectPipelineError("from orders\nwindow rn = ROW_NUMBER()").message)
      .toContain("expected 'over'");
    const wholeRelation = parse("from orders\nwindow total = SUM(quantity) over ()");
    expect(wholeRelation[1]).toMatchObject({ kind: "window", name: "total" });
  });

  it("rejects window functions outside a window stage", () => {
    const error = expectPipelineError(
      "from orders\nfilter ROW_NUMBER() over (partition by customer) = 1");
    expect(error.message).toContain("only valid in a window stage");
  });
});

describe("parser: select star and integers", () => {
  it("allows star with additional named expressions", () => {
    const program = parse("from orders\nselect *, total = quantity * unit_price");
    expect(program[1]).toMatchObject({ kind: "select", star: true, items: [{ alias: "total" }] });
  });

  it("requires integer counts for skip/take", () => {
    expect(expectPipelineError("from orders\ntake 12.5").message)
      .toContain("non-negative integer");
    expect(expectPipelineError("from orders\nskip -1").message)
      .toContain("non-negative integer");
  });

  it("accepts zero counts and rejects unsafe integers", () => {
    const program = parse("from orders\nskip 0\ntake 0");
    expect(program[1]).toMatchObject({ kind: "skip", count: 0 });
    expect(program[2]).toMatchObject({ kind: "take", count: 0 });
    expect(expectPipelineError("from orders\ntake 9007199254740993").message)
      .toContain("integer range");
  });

  it("parses select * as a star stage", () => {
    const program = parse("from orders\nselect *");
    const select = program[1] as SelectStage;
    expect(select.star).toBe(true);
    expect(select.items).toEqual([]);
  });
});

describe("parser: separators, spans, and segments", () => {
  it("parses CRLF sources", () => {
    const program = parse("from orders\r\nfilter quantity > 1\r\nsort order_id\r\n");
    expect(program.map((stage) => stage.kind)).toEqual(["from", "filter", "sort"]);
  });

  it("ignores comment-only lines and comments between stages", () => {
    const program = parse("# lead\nfrom orders -- tail\n# between\nsort order_id\n");
    expect(program.map((stage) => stage.kind)).toEqual(["from", "sort"]);
  });

  it("spans pipe-separated stages on one line without overlap", () => {
    const program = parse("from orders | take 2");
    expect(program.map((stage) => stage.kind)).toEqual(["from", "take"]);
    const fromStage = program[0];
    const takeStage = program[1];
    expect(fromStage?.span.end.column).toBeLessThanOrEqual(takeStage?.span.start.column ?? 0);
  });

  it("spans comparison and IS NULL expressions", () => {
    const comparison = filterCondition("a = 1");
    expect(comparison.span).toMatchObject({ start: { column: 8 }, end: { column: 13 } });
    const isNull = filterCondition("a IS NOT NULL");
    expect(isNull.span.end.column).toBe(21);
  });

  it("restarts phase, tail, and singletons after a sql stage", () => {
    const program = parse(
      'from orders\nsort order_id\nsql "SELECT * FROM __input__"\nsort -order_id');
    expect(program.map((stage) => stage.kind)).toEqual(["from", "sort", "sql", "sort"]);

    const grouped = parse(
      'from orders\nsql "SELECT * FROM __input__"\n'
      + "filter quantity > 1\nsort order_id\ntake 2");
    expect(grouped.map((stage) => stage.kind))
      .toEqual(["from", "sql", "filter", "sort", "take"]);
  });

  it("keeps from global across a sql stage", () => {
    expectPipelineError('from orders\nsql "SELECT * FROM __input__"\nfrom orders');
  });

  it("accepts WITH and WITH RECURSIVE in the hatch", () => {
    const withQuery = parse(
      'from orders\nsql "WITH RECURSIVE r AS (SELECT 1 AS n) SELECT n FROM r"');
    expect(withQuery[1]?.kind).toBe("sql");
  });

  it("allows a window after the tail (documented behavior)", () => {
    const program = parse(
      "from orders\nsort order_id\n"
      + "window rn = ROW_NUMBER() over (order by order_id)");
    expect(program.map((stage) => stage.kind)).toEqual(["from", "sort", "window"]);
  });
});
