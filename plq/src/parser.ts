/**
 * PLQ parser (plan step 4, audit batch B6).
 *
 * Hand-written recursive descent over the token stream from `tokenize`,
 * implementing docs/grammar.md v1.1: the stage grammar (§3), the ordering
 * rules (§4), and the expression grammar with its binding rules (§5).
 *
 * Errors are located: syntax errors inside a stage are re-wrapped as a
 * stage-located `PipelineError`, so every failure names the stage ordinal,
 * keyword, line, and column.
 */

import {
  spanning,
  tokenSpan,
  type AggregateStage,
  type BetweenExpr,
  type BinaryExpr,
  type BinaryOperator,
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
  type NamedAggregate,
  type NamedExpr,
  type Program,
  type RawSqlStage,
  type SelectItemNode,
  type SelectStage,
  type SkipStage,
  type SortKey,
  type SortStage,
  type SourceRef,
  type Span,
  type Stage,
  type StageBase,
  type TakeStage,
  type UnaryExpr,
  type WindowCall,
  type WindowStage,
} from "./ast.js";
import { ParseError, PipelineError, PlqError } from "./errors.js";
import { tokenize, type Token } from "./lexer.js";

const STAGE_STARTS = new Set([
  "from", "join", "left", "filter", "derive", "group", "aggregate", "having",
  "window", "select", "sort", "skip", "take", "distinct", "sql",
]);

const AGGREGATES = new Set(["count", "sum", "avg", "min", "max"]);

const WINDOW_FUNCTIONS = new Set([
  "row_number", "rank", "dense_rank", "lag", "lead", "sum", "avg", "count", "min", "max",
]);

const COMPARISONS = new Set(["=", "==", "!=", "<>", "<", "<=", ">", ">="]);

const NEGATABLE_TAILS = new Set(["in", "like", "between"]);

const SINGLETONS = new Set([
  "group by", "aggregate", "having", "select", "skip", "take", "distinct",
]);

export function parseProgram(source: string): Program {
  const tokens = tokenize(source);
  const rawStages = splitStages(tokens);
  if (rawStages.length === 0) {
    const eof = tokens[tokens.length - 1] as Token;
    throw new ParseError("a pipeline needs at least a from stage", eof.line, eof.column);
  }

  const entries = rawStages.map((stageTokens) => ({
    tokens: stageTokens,
    ...stageHeader(stageTokens),
  }));

  const stages: Stage[] = [];
  const seen = new Map<string, number>();
  let grouped = false;
  let aggregated = false;
  let projected = false;
  let tailStarted = false;
  let sortSeen = false;

  for (let position = 0; position < entries.length; position += 1) {
    const entry = entries[position] as (typeof entries)[number];
    const { tokens: stageTokens, keyword, rest } = entry;
    const index = position + 1;
    const first = stageTokens[0] as Token;
    const span = spanning(tokenSpan(first), tokenSpan(stageTokens[stageTokens.length - 1] as Token));
    const fail = (message: string, at?: Token): PipelineError =>
      new PipelineError(message, {
        index, keyword,
        line: (at ?? first).line,
        column: (at ?? first).column,
      });

    const nextKeyword = entries[position + 1]?.keyword;

    if (index === 1 && keyword !== "from") {
      throw fail("a pipeline must start with 'from'");
    }
    if (keyword === "from" && seen.has("from")) {
      throw fail("'from' may appear only once in a pipeline");
    }
    if (SINGLETONS.has(keyword) && seen.has(keyword)) {
      throw fail(`'${keyword}' may appear only once (already at stage ${seen.get(keyword)})`);
    }
    if (keyword === "sort" && sortSeen) {
      throw fail("'sort' may appear only once; use multiple keys: sort a, -b");
    }
    if (projected && !["sort", "skip", "take", "distinct"].includes(keyword)) {
      throw fail(`${keyword} cannot appear after select`);
    }
    if (tailStarted && ["join", "left join", "filter", "derive", "group by",
                        "aggregate", "having", "select", "distinct"].includes(keyword)) {
      throw fail(`${keyword} cannot appear after sort/skip/take; move it earlier`);
    }
    if (keyword === "sort" && (seen.has("skip") || seen.has("take"))) {
      throw fail("sort must appear before skip/take");
    }
    if (keyword === "skip" && seen.has("take")) {
      throw fail("skip must appear before take");
    }
    if ((keyword === "join" || keyword === "left join") && grouped) {
      throw fail("join cannot appear after group by");
    }
    if (keyword === "filter" && grouped) {
      throw fail("filter cannot appear after group by; use having");
    }
    if (keyword === "group by" && nextKeyword !== "aggregate") {
      throw fail("group by must be immediately followed by aggregate");
    }
    if (keyword === "aggregate" && !grouped) {
      throw fail("aggregate requires a preceding group by (they form one unit)");
    }
    if (keyword === "having" && !aggregated) {
      throw fail("having requires a preceding aggregate (group by + aggregate)");
    }

    const reader = new TokenReader(rest, first);
    const context = { inAggregate: false };
    let stage: Stage;
    try {
      stage = parseStageBody(keyword, reader, context, index, span, grouped);
    } catch (error) {
      if (error instanceof PipelineError) throw error;
      if (error instanceof PlqError) {
        throw new PipelineError(error.detail, {
          index, keyword,
          line: error.line ?? 1,
          column: error.column ?? 1,
        });
      }
      throw error;
    }
    stages.push(stage);

    if (keyword === "from" || SINGLETONS.has(keyword) || keyword === "sort") {
      seen.set(keyword, index);
    }
    if (keyword === "group by") grouped = true;
    if (keyword === "aggregate") aggregated = true;
    if (keyword === "select") projected = true;
    if (keyword === "sort") sortSeen = true;
    if (keyword === "sort" || keyword === "skip" || keyword === "take") tailStarted = true;
    if (keyword === "sql") {
      grouped = false;
      aggregated = false;
      projected = false;
      tailStarted = false;
      sortSeen = false;
      for (const key of [...seen.keys()]) {
        if (key !== "from") seen.delete(key);
      }
    }
  }

  return stages;
}

// --------------------------------------------------------------------------- //
// Stage splitting (newline / "|" at parenthesis depth zero)
// --------------------------------------------------------------------------- //

function splitStages(tokens: readonly Token[]): Token[][] {
  const stages: Token[][] = [];
  let current: Token[] = [];
  let depth = 0;
  for (const token of tokens) {
    if (token.type === "eof") break;
    const isSeparator = token.type === "newline" || (token.type === "operator" && token.value === "|");
    if (isSeparator) {
      if (depth > 0) {
        if (token.type === "newline") {
          throw new ParseError(
            "a stage cannot span lines; keep parenthesized expressions on one line",
            token.line, token.column);
        }
        throw new ParseError("unexpected '|' inside parentheses", token.line, token.column);
      }
      if (current.length > 0) {
        stages.push(current);
        current = [];
      }
      continue;
    }
    if (token.type === "punctuation" && token.value === "(") depth += 1;
    if (token.type === "punctuation" && token.value === ")") depth -= 1;
    current.push(token);
  }
  if (current.length > 0) stages.push(current);
  return stages;
}

function stageHeader(tokens: readonly Token[]): { keyword: string; rest: Token[] } {
  const first = tokens[0] as Token;
  if (first.type !== "keyword" || !STAGE_STARTS.has(first.value)) {
    if (first.type === "identifier") {
      const lowered = first.value.toLowerCase();
      if (["right", "full", "cross"].includes(lowered)) {
        throw new ParseError(
          `'${lowered} join' is not available in the pipeline grammar yet; use 'join' or 'left join'`,
          first.line, first.column);
      }
      throw new ParseError(`expected a stage keyword; found identifier '${first.value}'`,
                           first.line, first.column);
    }
    throw new ParseError(`expected a stage keyword; found ${describe(first)}`,
                         first.line, first.column);
  }
  if (first.value === "left") {
    const second = tokens[1];
    if (!second || second.type !== "keyword" || second.value !== "join") {
      throw new ParseError("'left' must be followed by 'join'", first.line, first.column);
    }
    return { keyword: "left join", rest: tokens.slice(2) };
  }
  if (first.value === "group") {
    const second = tokens[1];
    if (!second || second.type !== "keyword" || second.value !== "by") {
      throw new ParseError("'group' must be followed by 'by'", first.line, first.column);
    }
    return { keyword: "group by", rest: tokens.slice(2) };
  }
  return { keyword: first.value, rest: tokens.slice(1) };
}

// --------------------------------------------------------------------------- //
// Token cursor
// --------------------------------------------------------------------------- //

class TokenReader {
  private readonly tokens: readonly Token[];
  private readonly start: Token;
  private position = 0;
  lastConsumed: Token | null = null;

  constructor(tokens: readonly Token[], start: Token) {
    this.tokens = tokens;
    this.start = start;
  }

  get done(): boolean {
    return this.position >= this.tokens.length;
  }

  peek(offset = 0): Token | null {
    return this.tokens[this.position + offset] ?? null;
  }

  advance(): Token {
    const token = this.tokens[this.position];
    if (!token) this.fail("unexpected end of stage");
    this.position += 1;
    this.lastConsumed = token;
    return token;
  }

  atKeyword(word: string): boolean {
    const token = this.peek();
    return token !== null && token.type === "keyword" && token.value === word;
  }

  matchKeyword(word: string): boolean {
    if (!this.atKeyword(word)) return false;
    this.advance();
    return true;
  }

  expectKeyword(word: string, what?: string): Token {
    const token = this.peek();
    if (!token || token.type !== "keyword" || token.value !== word) {
      this.fail(`expected '${word}'${what ? ` ${what}` : ""}`, token);
    }
    return this.advance();
  }

  atOperator(value: string): boolean {
    const token = this.peek();
    return token !== null && token.type === "operator" && token.value === value;
  }

  matchOperator(value: string): boolean {
    if (!this.atOperator(value)) return false;
    this.advance();
    return true;
  }

  expectOperator(value: string, what?: string): Token {
    const token = this.peek();
    if (!token || token.type !== "operator" || token.value !== value) {
      this.fail(`expected '${value}'${what ? ` ${what}` : ""}`, token);
    }
    return this.advance();
  }

  atPunct(value: string): boolean {
    const token = this.peek();
    return token !== null && token.type === "punctuation" && token.value === value;
  }

  matchPunct(value: string): boolean {
    if (!this.atPunct(value)) return false;
    this.advance();
    return true;
  }

  expectPunct(value: string, what?: string): Token {
    const token = this.peek();
    if (!token || token.type !== "punctuation" || token.value !== value) {
      this.fail(`expected '${value}'${what ? ` ${what}` : ""}`, token);
    }
    return this.advance();
  }

  expectIdentifier(what = "expected an identifier"): Token {
    const token = this.peek();
    if (!token || token.type !== "identifier") this.fail(what, token);
    return this.advance();
  }

  expectEnd(): void {
    if (!this.done) {
      const token = this.peek() as Token;
      this.fail(`unexpected ${describe(token)}`, token);
    }
  }

  fail(message: string, token?: Token | null): never {
    if (token) throw new ParseError(message, token.line, token.column);
    const last = this.lastConsumed;
    if (last) throw new ParseError(message, last.endLine, last.endColumn);
    throw new ParseError(message, this.start.line, this.start.column);
  }
}

function describe(token: Token): string {
  switch (token.type) {
    case "keyword":
      return `keyword '${token.value}'`;
    case "identifier":
      return `identifier '${token.value}'`;
    case "string":
      return "string literal";
    case "number":
      return `number '${token.value}'`;
    case "operator":
    case "punctuation":
      return `'${token.value}'`;
    default:
      return token.type;
  }
}

function spanFrom(reader: TokenReader, start: Token): Span {
  return spanning(tokenSpan(start), tokenSpan(reader.lastConsumed ?? start));
}

// --------------------------------------------------------------------------- //
// Stage bodies
// --------------------------------------------------------------------------- //

interface ExprContext {
  inAggregate: boolean;
}

function parseStageBody(
  keyword: string,
  reader: TokenReader,
  context: ExprContext,
  index: number,
  span: Span,
  grouped: boolean,
): Stage {
  switch (keyword) {
    case "from": {
      const source = parseSource(reader, "from source");
      reader.expectEnd();
      return { ...base(index, keyword, span), kind: "from", source } satisfies FromStage;
    }
    case "join":
    case "left join": {
      const source = parseSource(reader, "join source");
      reader.expectKeyword("on", "after the joined table");
      const on = parseExpression(reader, context);
      reader.expectEnd();
      return {
        ...base(index, keyword, span), kind: "join",
        joinKind: keyword === "left join" ? "left" : "inner", source, on,
      } satisfies JoinStage;
    }
    case "filter": {
      const condition = parseExpression(reader, context);
      reader.expectEnd();
      return { ...base(index, keyword, span), kind: "filter", condition } satisfies FilterStage;
    }
    case "derive": {
      const items = parseCommaList(reader, (r) => parseNamedExpr(r, "derive"));
      reader.expectEnd();
      return {
        ...base(index, keyword, span), kind: "derive",
        phase: grouped ? "group" : "row", items,
      } satisfies DeriveStage;
    }
    case "group by": {
      const keys = parseCommaList(reader, parseGroupKey);
      reader.expectEnd();
      return { ...base(index, keyword, span), kind: "group by", keys } satisfies GroupByStage;
    }
    case "aggregate": {
      const items = parseCommaList(reader, parseAggregateItem);
      reader.expectEnd();
      return { ...base(index, keyword, span), kind: "aggregate", items } satisfies AggregateStage;
    }
    case "having": {
      const condition = parseExpression(reader, context);
      reader.expectEnd();
      return { ...base(index, keyword, span), kind: "having", condition } satisfies HavingStage;
    }
    case "window": {
      const nameToken = reader.expectIdentifier("window needs a name: window name = FUNC(...) over (...)");
      reader.expectOperator("=", "after the window name");
      const funcToken = reader.expectIdentifier("expected a window function name");
      const func = funcToken.value;
      if (!WINDOW_FUNCTIONS.has(func.toLowerCase())) {
        reader.fail(`'${func}' is not a window function`, funcToken);
      }
      reader.expectPunct("(");
      let star = false;
      let args: Expr[] = [];
      if (reader.atOperator("*")) {
        reader.advance();
        if (func.toLowerCase() !== "count") reader.fail("only COUNT accepts '*'", funcToken);
        star = true;
      } else if (!reader.atPunct(")")) {
        args = parseExprList(reader, context);
      }
      reader.expectPunct(")");
      reader.expectKeyword("over", "after the window function call");
      reader.expectPunct("(");
      let partitionBy: Expr[] = [];
      let orderBy: SortKey[] = [];
      if (reader.matchKeyword("partition")) {
        reader.expectKeyword("by", "after 'partition'");
        partitionBy = parseExprList(reader, context);
      }
      if (reader.matchKeyword("order")) {
        reader.expectKeyword("by", "after 'order'");
        orderBy = parseCommaList(reader, parseSortKey);
      }
      if (partitionBy.length === 0 && orderBy.length === 0) {
        reader.fail("over requires at least one of 'partition by' or 'order by'");
      }
      reader.expectPunct(")", "to close the over clause");
      reader.expectEnd();
      const call: WindowCall = { func, args, star, partitionBy, orderBy };
      return { ...base(index, keyword, span), kind: "window", name: nameToken.value, call } satisfies WindowStage;
    }
    case "select": {
      if (reader.atOperator("*")) {
        const starToken = reader.advance();
        if (!reader.done) {
          reader.fail("'*' must be the only select item", starToken);
        }
        return { ...base(index, keyword, span), kind: "select", star: true, items: [] } satisfies SelectStage;
      }
      const items = parseCommaList(reader, parseSelectItem);
      reader.expectEnd();
      return { ...base(index, keyword, span), kind: "select", star: false, items } satisfies SelectStage;
    }
    case "sort": {
      const keys = parseCommaList(reader, parseSortKey);
      reader.expectEnd();
      return { ...base(index, keyword, span), kind: "sort", keys } satisfies SortStage;
    }
    case "skip": {
      const count = parseCount(reader, "skip");
      return { ...base(index, keyword, span), kind: "skip", count } satisfies SkipStage;
    }
    case "take": {
      const count = parseCount(reader, "take");
      return { ...base(index, keyword, span), kind: "take", count } satisfies TakeStage;
    }
    case "distinct": {
      reader.expectEnd();
      return { ...base(index, keyword, span), kind: "distinct" } satisfies DistinctStage;
    }
    case "sql": {
      const token = reader.peek();
      if (!token || token.type !== "string") {
        reader.fail('sql requires one string literal, as in sql "SELECT * FROM __input__"', token);
      }
      reader.advance();
      reader.expectEnd();
      const text = token.value;
      if (text.trim() === "") reader.fail("sql stage text is empty", token);
      if (text.includes(";")) {
        reader.fail("sql stage must be a single SELECT statement; ';' is not allowed", token);
      }
      const firstWord = text.trim().split(/\s+/, 1)[0]?.toUpperCase() ?? "";
      if (firstWord !== "SELECT" && firstWord !== "WITH") {
        reader.fail("sql stage must be a query; start it with SELECT or WITH", token);
      }
      return { ...base(index, keyword, span), kind: "sql", text } satisfies RawSqlStage;
    }
    default:
      reader.fail(`unknown stage '${keyword}'`);
  }
}

function base(index: number, keyword: string, span: Span): Omit<StageBase, "kind"> {
  return { index, keyword, span };
}

function parseSource(reader: TokenReader, what: string): SourceRef {
  const start = reader.peek();
  if (!start) reader.fail(`expected a table name for the ${what}`);
  if (start.type === "punctuation" && start.value === "(") {
    reader.fail(`a ${what} cannot be a subquery; the pipeline grammar reads tables`, start);
  }
  if (start.type !== "identifier") {
    reader.fail(`expected a table name for the ${what}`, start);
  }
  reader.advance();
  const parts = [start.value];
  while (reader.matchPunct(".")) {
    parts.push(reader.expectIdentifier("expected an identifier after '.'").value);
  }
  let alias: string | null = null;
  if (reader.matchKeyword("as")) {
    alias = reader.expectIdentifier("expected an alias after 'as'").value;
  }
  return { name: parts.join("."), alias, span: spanFrom(reader, start) };
}

function parseCommaList<T>(reader: TokenReader, parseItem: (reader: TokenReader) => T): T[] {
  const items = [parseItem(reader)];
  while (reader.matchPunct(",")) {
    items.push(parseItem(reader));
  }
  return items;
}

function parseNamedExpr(reader: TokenReader, what: string): NamedExpr {
  const start = reader.peek();
  const name = reader.expectIdentifier(`${what} entries need a name: name = expression`);
  reader.expectOperator("=", `after the ${what} name`);
  const expr = parseExpression(reader, { inAggregate: false });
  return { name: name.value, expr, span: spanFrom(reader, start as Token) };
}

function parseGroupKey(reader: TokenReader): NamedExpr {
  if (reader.done) reader.fail("group by requires at least one key");
  const start = reader.peek() as Token;
  if (start.type === "identifier") {
    const next = reader.peek(1);
    if (next && next.type === "operator" && next.value === "=") {
      const name = reader.advance().value;
      reader.advance();
      const expr = parseExpression(reader, { inAggregate: false });
      return { name, expr, span: spanFrom(reader, start) };
    }
    if (next && next.type === "punctuation" && next.value === ".") {
      reader.fail("group keys cannot be qualified names; write 'name = <expression>' instead", start);
    }
    reader.advance();
    return {
      name: start.value,
      expr: { kind: "name", name: start.value, span: tokenSpan(start) },
      span: tokenSpan(start),
    };
  }
  reader.fail("group keys must be a column name or name = expression", start);
}

function parseAggregateItem(reader: TokenReader): NamedAggregate {
  const start = reader.peek();
  if (!start) reader.fail("aggregate requires at least one named aggregate call");
  let name: string | null = null;
  const first = reader.peek();
  const second = reader.peek(1);
  if (first && first.type === "identifier"
      && second && second.type === "operator" && second.value === "=") {
    name = reader.advance().value;
    reader.advance();
  }
  const call = parseExpression(reader, { inAggregate: true });
  if (call.kind !== "call") {
    reader.fail("aggregate items must be an aggregate call: name = COUNT(*) etc.", start);
  }
  const lowered = call.name.toLowerCase();
  if (!AGGREGATES.has(lowered)) {
    reader.fail(`'${call.name}' is not an aggregate; use COUNT, SUM, AVG, MIN, or MAX`, start);
  }
  if (call.args.some(containsAggregateCall)) {
    reader.fail("aggregate arguments cannot contain aggregate calls", start);
  }
  if (name === null) {
    if (lowered === "count" && call.star) {
      name = "count";
    } else {
      reader.fail("aggregates must be named: name = AGG(expression)", start);
    }
  }
  return { name, call, span: spanFrom(reader, start) };
}

function parseSelectItem(reader: TokenReader): SelectItemNode {
  const start = reader.peek();
  if (!start) reader.fail("select requires at least one column or expression");
  if (start.type === "operator" && start.value === "*") {
    reader.fail("'*' must be the only select item", start);
  }
  const first = reader.peek();
  const second = reader.peek(1);
  if (first && first.type === "identifier"
      && second && second.type === "operator" && second.value === "=") {
    const alias = reader.advance().value;
    reader.advance();
    const expr = parseExpression(reader, { inAggregate: false });
    return { expr, alias, span: spanFrom(reader, start) };
  }
  const expr = parseExpression(reader, { inAggregate: false });
  return { expr, alias: null, span: spanFrom(reader, start) };
}

function parseSortKey(reader: TokenReader): SortKey {
  const start = reader.peek();
  if (!start) reader.fail("sort requires at least one key");
  let descending = false;
  if (reader.matchOperator("-")) descending = true;
  const expr = parseExpression(reader, { inAggregate: false });
  if (reader.matchKeyword("desc")) {
    descending = true;
  } else {
    reader.matchKeyword("asc");
  }
  return { expr, descending, span: spanFrom(reader, start) };
}

function parseCount(reader: TokenReader, keyword: string): number {
  const token = reader.peek();
  if (!token || token.type !== "number" || token.value.includes(".")) {
    reader.fail(`${keyword} requires a non-negative integer`, token);
  }
  reader.advance();
  reader.expectEnd();
  const value = Number(token.value);
  if (!Number.isSafeInteger(value)) {
    reader.fail(`${keyword} exceeds the supported integer range`, token);
  }
  return value;
}

// --------------------------------------------------------------------------- //
// Expressions (grammar §5)
// --------------------------------------------------------------------------- //

function parseExpression(reader: TokenReader, context: ExprContext): Expr {
  return parseOr(reader, context);
}

function parseOr(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek();
  if (!start) reader.fail("expected an expression");
  let left = parseAnd(reader, context);
  while (reader.matchKeyword("or")) {
    const right = parseAnd(reader, context);
    left = { kind: "binary", op: "or", left, right, span: spanFrom(reader, start) };
  }
  return left;
}

function parseAnd(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek();
  if (!start) reader.fail("expected an expression");
  let left = parseNot(reader, context);
  while (reader.matchKeyword("and")) {
    const right = parseNot(reader, context);
    left = { kind: "binary", op: "and", left, right, span: spanFrom(reader, start) };
  }
  return left;
}

function parseNot(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek();
  if (!start) reader.fail("expected an expression");
  if (reader.matchKeyword("not")) {
    const operand = parseComparison(reader, context);
    return { kind: "unary", op: "not", operand, span: spanFrom(reader, start) };
  }
  return parseComparison(reader, context);
}

function parseComparison(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek();
  if (!start) reader.fail("expected an expression");
  const left = parseAdditive(reader, context);
  const withTail = parseComparisonTail(reader, context, left);
  if (startsComparison(reader)) {
    reader.fail("chained comparisons are not supported; combine them with AND (or use BETWEEN)");
  }
  return withTail;
}

function startsComparison(reader: TokenReader): boolean {
  const token = reader.peek();
  if (!token) return false;
  if (token.type === "operator" && COMPARISONS.has(token.value)) return true;
  if (token.type === "keyword") {
    if (token.value === "is" || token.value === "in"
        || token.value === "like" || token.value === "between") {
      return true;
    }
    if (token.value === "not") {
      const next = reader.peek(1);
      return next !== null && next.type === "keyword" && NEGATABLE_TAILS.has(next.value);
    }
  }
  return false;
}

function parseComparisonTail(reader: TokenReader, context: ExprContext, left: Expr): Expr {
  const token = reader.peek();
  if (!token) return left;
  if (token.type === "operator" && COMPARISONS.has(token.value)) {
    const op = reader.advance().value as BinaryOperator;
    const right = parseAdditive(reader, context);
    return { kind: "binary", op, left, right, span: spanning(left.span, right.span) } satisfies BinaryExpr;
  }
  if (reader.atKeyword("is")) {
    reader.advance();
    const negated = reader.matchKeyword("not");
    reader.expectKeyword("null", "after 'is'");
    return { kind: "is null", negated, value: left, span: spanFrom(reader, startOf(left)) } satisfies IsNullExpr;
  }
  let negated = false;
  if (reader.atKeyword("not")) {
    const next = reader.peek(1);
    if (next && next.type === "keyword" && NEGATABLE_TAILS.has(next.value)) {
      reader.advance();
      negated = true;
    }
  }
  if (reader.atKeyword("in")) {
    reader.advance();
    reader.expectPunct("(");
    const options = parseExprList(reader, context);
    reader.expectPunct(")", "to close the IN list");
    return { kind: "in", negated, value: left, options,
             span: spanning(left.span, tokenSpan(reader.lastConsumed as Token)) } satisfies InExpr;
  }
  if (reader.atKeyword("like")) {
    reader.advance();
    const pattern = parseAdditive(reader, context);
    return { kind: "like", negated, value: left, pattern, span: spanning(left.span, pattern.span) } satisfies LikeExpr;
  }
  if (reader.atKeyword("between")) {
    reader.advance();
    const lower = parseAdditive(reader, context);
    reader.expectKeyword("and", "between the BETWEEN bounds");
    const upper = parseAdditive(reader, context);
    return { kind: "between", negated, value: left, lower, upper,
             span: spanning(left.span, upper.span) } satisfies BetweenExpr;
  }
  return left;
}

function startOf(expr: Expr): Token {
  // Reconstruct a token-shaped start from a span for spanFrom on tails.
  return {
    type: "keyword", value: "", line: expr.span.start.line, column: expr.span.start.column,
    endLine: expr.span.start.line, endColumn: expr.span.start.column,
  };
}

function parseAdditive(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek();
  if (!start) reader.fail("expected an expression");
  let left = parseMultiplicative(reader, context);
  while (reader.atOperator("+") || reader.atOperator("-") || reader.atOperator("||")) {
    const op = reader.advance().value as BinaryOperator;
    const right = parseMultiplicative(reader, context);
    left = { kind: "binary", op, left, right, span: spanFrom(reader, start) };
  }
  return left;
}

function parseMultiplicative(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek();
  if (!start) reader.fail("expected an expression");
  let left = parseUnary(reader, context);
  while (reader.atOperator("*") || reader.atOperator("/") || reader.atOperator("%")) {
    const op = reader.advance().value as BinaryOperator;
    const right = parseUnary(reader, context);
    left = { kind: "binary", op, left, right, span: spanFrom(reader, start) };
  }
  return left;
}

function parseUnary(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek();
  if (!start) reader.fail("expected an expression");
  if (reader.atOperator("+") || reader.atOperator("-")) {
    const op = reader.advance().value as "+" | "-";
    const operand = parseUnary(reader, context);
    return { kind: "unary", op, operand, span: spanFrom(reader, start) } satisfies UnaryExpr;
  }
  return parsePrimary(reader, context);
}

function parsePrimary(reader: TokenReader, context: ExprContext): Expr {
  const token = reader.peek();
  if (!token) reader.fail("expected an expression");
  if (token.type === "number") {
    reader.advance();
    return { kind: "literal", value: Number(token.value), span: tokenSpan(token) };
  }
  if (token.type === "string") {
    reader.advance();
    return { kind: "literal", value: token.value, span: tokenSpan(token) };
  }
  if (token.type === "keyword") {
    switch (token.value) {
      case "true":
      case "false":
      case "null": {
        reader.advance();
        const value = token.value === "null" ? null : token.value === "true";
        return { kind: "literal", value, span: tokenSpan(token) };
      }
      case "case":
        return parseCase(reader, context);
      case "cast":
        return parseCast(reader, context);
      default:
        reader.fail(`unexpected keyword '${token.value}' in expression`, token);
    }
  }
  if (token.type === "identifier") {
    return parseNameOrCall(reader, context);
  }
  if (token.type === "punctuation" && token.value === "(") {
    reader.advance();
    const inner = parseExpression(reader, context);
    reader.expectPunct(")", "to close the expression");
    return inner;
  }
  reader.fail(`unexpected ${describe(token)} in expression`, token);
}

function parseNameOrCall(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek() as Token;
  const nameToken = reader.advance();
  const parts = [nameToken.value];
  while (reader.matchPunct(".")) {
    parts.push(reader.expectIdentifier("expected an identifier after '.'").value);
  }
  const lowered = nameToken.value.toLowerCase();
  if (!reader.atPunct("(")) {
    return { kind: "name", name: parts.join("."), span: spanFrom(reader, start) };
  }
  if (parts.length > 1) {
    reader.fail("function names cannot be qualified", nameToken);
  }
  reader.advance();
  const distinct = reader.matchKeyword("distinct");
  let star = false;
  let emptyArgs = false;
  let args: Expr[] = [];
  if (reader.atOperator("*")) {
    reader.advance();
    star = true;
  } else if (reader.atPunct(")")) {
    emptyArgs = true;
  } else {
    args = parseExprList(reader, context);
  }
  reader.expectPunct(")", "to close the call");
  if (reader.atKeyword("over")) {
    reader.fail("a window function is only valid in a window stage", reader.peek() as Token);
  }
  if (emptyArgs) {
    reader.fail("function calls need at least one argument (or COUNT(*))", nameToken);
  }
  if (star && lowered !== "count") reader.fail("only COUNT accepts '*'", nameToken);
  if (distinct && lowered !== "count") {
    reader.fail("DISTINCT is only valid for COUNT(DISTINCT expression)", nameToken);
  }
  if (distinct && star) {
    reader.fail("COUNT(DISTINCT *) is not valid; name a column instead", nameToken);
  }
  if (!context.inAggregate && AGGREGATES.has(lowered)) {
    reader.fail(
      `aggregate '${nameToken.value.toUpperCase()}' is only valid inside an aggregate stage; reference the aggregate name instead`,
      nameToken);
  }
  return { kind: "call", name: nameToken.value, distinct, star, args, span: spanFrom(reader, start) } satisfies FuncCallExpr;
}

function parseCase(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek() as Token;
  reader.advance();
  const branches = [];
  while (reader.matchKeyword("when")) {
    const when = parseExpression(reader, context);
    reader.expectKeyword("then", "in the CASE branch");
    const then = parseExpression(reader, context);
    branches.push({ when, then });
  }
  if (branches.length === 0) reader.fail("case requires at least one 'when' branch", start);
  let elseExpr: Expr | null = null;
  if (reader.matchKeyword("else")) {
    elseExpr = parseExpression(reader, context);
  }
  reader.expectKeyword("end", "to close the case expression");
  return { kind: "case", branches, elseExpr, span: spanFrom(reader, start) } satisfies CaseExpr;
}

function parseCast(reader: TokenReader, context: ExprContext): Expr {
  const start = reader.peek() as Token;
  reader.advance();
  reader.expectPunct("(", "after CAST");
  const operand = parseExpression(reader, context);
  reader.expectKeyword("as", "in the CAST expression");
  const typeName = reader.expectIdentifier("expected a type name after 'as'");
  reader.expectPunct(")", "to close the CAST expression");
  return { kind: "cast", operand, typeName: typeName.value, span: spanFrom(reader, start) } satisfies CastExpr;
}

function parseExprList(reader: TokenReader, context: ExprContext): Expr[] {
  const items = [parseExpression(reader, context)];
  while (reader.matchPunct(",")) {
    items.push(parseExpression(reader, context));
  }
  return items;
}

function containsAggregateCall(expr: Expr): boolean {
  switch (expr.kind) {
    case "call":
      if (AGGREGATES.has(expr.name.toLowerCase())) return true;
      return expr.args.some(containsAggregateCall);
    case "binary":
      return containsAggregateCall(expr.left) || containsAggregateCall(expr.right);
    case "unary":
      return containsAggregateCall(expr.operand);
    case "like":
      return containsAggregateCall(expr.value) || containsAggregateCall(expr.pattern);
    case "in":
      return containsAggregateCall(expr.value) || expr.options.some(containsAggregateCall);
    case "between":
      return containsAggregateCall(expr.value)
        || containsAggregateCall(expr.lower) || containsAggregateCall(expr.upper);
    case "is null":
      return containsAggregateCall(expr.value);
    case "case":
      return expr.branches.some((branch) =>
          containsAggregateCall(branch.when) || containsAggregateCall(branch.then))
        || (expr.elseExpr !== null && containsAggregateCall(expr.elseExpr));
    case "cast":
      return containsAggregateCall(expr.operand);
    default:
      return false;
  }
}
