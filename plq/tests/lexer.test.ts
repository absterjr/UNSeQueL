import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { LexError, tokenize, type Token } from "../src/lexer.js";

/**
 * Step 3: the lexer.
 *
 * Done-when: all 21 reference queries tokenize without errors, and
 * deliberately malformed inputs produce token-level errors with positions.
 */

const REFERENCE = join(__dirname, "..", "examples", "reference");

const referenceFiles = readdirSync(REFERENCE)
  .filter((name) => name.endsWith(".plq"))
  .sort();

function reference(name: string): string {
  return readFileSync(join(REFERENCE, name), "utf8");
}

function types(tokens: Token[]): string[] {
  return tokens.map((token) => token.type);
}

function values(tokens: Token[]): string[] {
  return tokens.map((token) => token.value);
}

describe("lexer: reference corpus", () => {
  it("tokenizes all 21 reference queries without errors", () => {
    expect(referenceFiles).toHaveLength(21);
    for (const file of referenceFiles) {
      const tokens = tokenize(reference(file));
      expect(tokens.at(-1)?.type, file).toBe("eof");
      expect(tokens.length, file).toBeGreaterThan(1);
    }
  });

  it("tokenizes CRLF input with correct line numbers", () => {
    const tokens = tokenize("from orders\r\nfilter quantity > 1\r\nsort -quantity\r\n");
    const stageStarts = tokens.filter(
      (token) => token.type === "keyword" && ["from", "filter", "sort"].includes(token.value),
    );
    expect(stageStarts.map((token) => [token.value, token.line])).toEqual([
      ["from", 1],
      ["filter", 2],
      ["sort", 3],
    ]);
  });

  it("q20 tokenizes into the expected stage vocabulary", () => {
    const tokens = tokenize(reference("q20_full_pipeline.plq"));
    const keywords = tokens.filter((token) => token.type === "keyword").map((t) => t.value);
    expect(keywords).toContain("from");
    expect(keywords).toContain("join");
    expect(keywords).toContain("group");
    expect(keywords).toContain("by");
    expect(keywords).toContain("aggregate");
    expect(keywords).toContain("having");
    expect(keywords).toContain("take");
    expect(keywords).not.toContain("filter");
  });

  it("q21 keeps the raw SQL as one string token", () => {
    const tokens = tokenize(reference("q21_sql_hatch.plq"));
    const strings = tokens.filter((token) => token.type === "string");
    expect(strings).toHaveLength(1);
    expect(strings[0]?.value).toContain("ROW_NUMBER() OVER");
    expect(strings[0]?.value).toContain("__input__");
  });
});

describe("lexer: tokens", () => {
  it("emits keywords in canonical lowercase and preserves identifier case", () => {
    const tokens = tokenize("FROM Orders");
    expect(tokens[0]).toMatchObject({ type: "keyword", value: "from", line: 1, column: 1 });
    expect(tokens[1]).toMatchObject({ type: "identifier", value: "Orders", line: 1, column: 6 });
    expect(tokens[2]?.type).toBe("eof");
  });

  it("distinguishes integers and reals", () => {
    expect(values(tokenize("take 12"))).toEqual(["take", "12", ""]);
    const real = tokenize("filter price > 12.5");
    expect(real.find((token) => token.type === "number")?.value).toBe("12.5");
  });

  it("prefers || over two pipes and recognizes two-char operators", () => {
    const tokens = tokenize("derive x = a || b");
    expect(tokens.some((token) => token.value === "||")).toBe(true);
    expect(tokens.filter((token) => token.value === "|")).toHaveLength(0);
    for (const op of ["!=", "<>", "==", "<=", ">="]) {
      expect(tokenize(`filter a ${op} b`).some((token) => token.value === op)).toBe(true);
    }
  });

  it("emits | as a stage separator token", () => {
    const tokens = tokenize("from orders | take 2");
    expect(tokens.some((token) => token.type === "operator" && token.value === "|")).toBe(true);
  });

  it("keeps qualified names as identifier, dot, identifier", () => {
    const tokens = tokenize("join products on product_id = products.product_id");
    expect(types(tokens)).toEqual([
      "keyword", "identifier", "keyword", "identifier", "operator",
      "identifier", "punctuation", "identifier", "eof",
    ]);
  });

  it("unescapes strings and accepts doubled quotes", () => {
    expect(tokenize("filter a = 'it''s'")[3]).toMatchObject({ type: "string", value: "it's" });
    expect(tokenize('filter a = "say \\"hi\\""')[3]).toMatchObject({ value: 'say "hi"' });
    expect(tokenize("filter a = 'x\\ny'")[3]?.value).toBe("x\ny");
    expect(tokenize("filter a = 'c:\\\\dir'")[3]?.value).toBe("c:\\dir");
  });

  it("drops # and -- comments but keeps line structure", () => {
    const tokens = tokenize("from orders # comment\n-- another\nsort order_id\n");
    expect(values(tokens)).not.toContain("comment");
    expect(types(tokens).filter((type) => type === "newline")).toHaveLength(3);
  });

  it("tracks newlines and columns", () => {
    const tokens = tokenize("from orders\nsort -quantity");
    const sort = tokens.find((token) => token.value === "sort");
    expect(sort).toMatchObject({ line: 2, column: 1 });
    const minus = tokens.find((token) => token.value === "-");
    expect(minus).toMatchObject({ line: 2, column: 6 });
  });
});

describe("lexer: errors", () => {
  it("rejects an unexpected character with its position", () => {
    try {
      tokenize("from orders\nfilter quantity > @");
      expect.unreachable("expected LexError");
    } catch (error) {
      expect(error).toBeInstanceOf(LexError);
      const lexError = error as LexError;
      expect(lexError.line).toBe(2);
      expect(lexError.column).toBe(19);
      expect(lexError.message).toContain('unexpected character "@"');
    }
  });

  it("rejects a lone ! with its position", () => {
    try {
      tokenize("from orders\nfilter a ! b");
      expect.unreachable("expected LexError");
    } catch (error) {
      const lexError = error as LexError;
      expect(lexError.line).toBe(2);
      expect(lexError.column).toBe(10);
    }
  });

  it("rejects an unterminated string at end of input", () => {
    try {
      tokenize("from orders\nfilter name = 'abc");
      expect.unreachable("expected LexError");
    } catch (error) {
      const lexError = error as LexError;
      expect(lexError.line).toBe(2);
      expect(lexError.column).toBe(15);
      expect(lexError.message).toContain("unterminated string");
    }
  });

  it("rejects a string spanning a line break", () => {
    try {
      tokenize("from orders\nfilter name = 'a\nb'");
      expect.unreachable("expected LexError");
    } catch (error) {
      const lexError = error as LexError;
      expect(lexError.message).toContain("single-line");
    }
  });

  it("rejects an unknown escape sequence with its position", () => {
    try {
      tokenize("from orders\nfilter a = 'x\\qy'");
      expect.unreachable("expected LexError");
    } catch (error) {
      const lexError = error as LexError;
      expect(lexError.line).toBe(2);
      expect(lexError.column).toBe(14);
      expect(lexError.message).toContain("unknown escape sequence \\q");
    }
  });
});
