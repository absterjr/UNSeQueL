import { describe, expect, it } from "vitest";
import { loweringUnits, unitIndexFor, type StageRef } from "../src/ast.js";
import { LexError, ParseError, PipelineError, PlqError } from "../src/errors.js";

/**
 * Audit batch B3: the contracts the parser (step 4) builds on.
 * - lowering units implement grammar §1.1 (group by + aggregate share a CTE)
 * - the error hierarchy is uniform and stage-located
 */

function ref(kind: StageRef["kind"], index: number): StageRef {
  return { kind, index };
}

describe("lowering units (grammar §1.1)", () => {
  it("maps group by + aggregate to one CTE", () => {
    const units = loweringUnits([
      ref("from", 1),
      ref("group by", 2),
      ref("aggregate", 3),
      ref("having", 4),
      ref("sort", 5),
      ref("take", 6),
    ]);
    expect(units.map((unit) => unit.cte)).toEqual([
      "stage_1", "stage_2", "stage_3", "stage_4", "stage_5",
    ]);
    expect(units[1]).toMatchObject({ ordinal: 2, stages: [2, 3] });
  });

  it("keeps every other stage as its own unit", () => {
    const kinds: StageRef["kind"][] = [
      "from", "join", "derive", "filter", "select", "distinct", "sort", "skip", "take",
    ];
    const units = loweringUnits(kinds.map((kind, i) => ref(kind, i + 1)));
    expect(units).toHaveLength(9);
    expect(units.every((unit) => unit.stages.length === 1)).toBe(true);
  });

  it("maps a grammar stage ordinal to its unit", () => {
    const stages = [
      ref("from", 1), ref("group by", 2), ref("aggregate", 3), ref("sort", 4),
    ];
    expect(unitIndexFor(stages, 1)).toBe(1);
    expect(unitIndexFor(stages, 2)).toBe(2);
    expect(unitIndexFor(stages, 3)).toBe(2);
    expect(unitIndexFor(stages, 4)).toBe(3);
    expect(() => unitIndexFor(stages, 5)).toThrow(RangeError);
  });
});

describe("error hierarchy", () => {
  it("formats stage-located pipeline errors", () => {
    const error = new PipelineError("join cannot appear after group", {
      index: 3, keyword: "join", line: 3, column: 1,
    });
    expect(error).toBeInstanceOf(PlqError);
    expect(error.stageIndex).toBe(3);
    expect(error.stageKeyword).toBe("join");
    expect(error.line).toBe(3);
    expect(error.message).toBe(
      "stage 3 (join): join cannot appear after group at line 3, column 1",
    );
  });

  it("keeps ParseError and LexError as PlqErrors", () => {
    const parse = new ParseError("expected expression", 2, 5);
    expect(parse).toBeInstanceOf(PlqError);
    expect(parse.message).toBe("expected expression at line 2, column 5");
    expect(new LexError("unexpected character", 1, 1)).toBeInstanceOf(PlqError);
  });
});
