"""Pipeline front-end: lower the stage IR onto the ordered-clause Query AST.

The pipeline surface is one transform per line (see docs/pipeline-spec.md). It
adds no execution semantics: `parse_pipeline_stages` builds a typed stage list
and `lower_to_query` collapses it onto the existing `Query` AST, which the
in-memory engine already knows how to run.
"""

from __future__ import annotations

from .engine import execute
from .expressions import (Between, Binary, Call, Case, Cast, Expr, Identifier, InList,
                          Literal, Unary, Wildcard)
from .model import Table
from .parser import FromSpec, OrderItem, Query, SelectItem
from .pipeline_ir import (Derive, Distinct, From, Group, Join, PipelineError, RawSql,
                          Select, Skip, Sort, Stage, Take, Where, parse_pipeline_stages)
from .sql_runtime import run_sql_stage

__all__ = ["parse_pipeline", "lower_to_query", "parse_pipeline_stages", "PipelineError",
           "execute_pipeline_stages"]


def _inline(expr: Expr, names: dict[str, Expr]) -> Expr:
    if isinstance(expr, Identifier):
        return names.get(expr.name, expr)
    if isinstance(expr, (Literal, Wildcard)):
        return expr
    if isinstance(expr, Unary):
        return Unary(expr.op, _inline(expr.operand, names))
    if isinstance(expr, Binary):
        return Binary(expr.op, _inline(expr.left, names), _inline(expr.right, names))
    if isinstance(expr, Call):
        return Call(expr.name, tuple(_inline(arg, names) for arg in expr.args), expr.distinct)
    if isinstance(expr, InList):
        return InList(_inline(expr.value, names),
                      tuple(_inline(option, names) for option in expr.options), expr.negated)
    if isinstance(expr, Between):
        return Between(_inline(expr.value, names), _inline(expr.lower, names),
                       _inline(expr.upper, names), expr.negated)
    if isinstance(expr, Case):
        branches = tuple((_inline(condition, names), _inline(result, names))
                         for condition, result in expr.branches)
        return Case(branches, _inline(expr.else_expr, names))
    if isinstance(expr, Cast):
        return Cast(_inline(expr.operand, names), expr.type_name)
    return expr


def _combine(left: Expr | None, right: Expr) -> Expr:
    return right if left is None else Binary("AND", left, right)


class _Builder:
    def __init__(self) -> None:
        self.source = None
        self.joins = []
        self.where: Expr | None = None
        self.having: Expr | None = None
        self.group_keys: list[tuple[str, Expr]] = []
        self.aggregates: list[tuple[str, Call]] = []
        self.row_derives: dict[str, Expr] = {}
        self.group_derives: list[tuple[str, Expr]] = []
        self.final_select: list[SelectItem] | None = None
        self.order_by: list[OrderItem] = []
        self.limit: int | None = None
        self.offset = 0
        self.distinct = False
        self.grouped = False

    @property
    def _group_names(self) -> dict[str, Expr]:
        names: dict[str, Expr] = {name: expr for name, expr in self.group_keys}
        names.update({name: expr for name, expr in self.aggregates})
        names.update({name: expr for name, expr in self.group_derives})
        return names

    def add(self, stage: Stage) -> None:
        if isinstance(stage, From):
            self.source = stage.source
        elif isinstance(stage, Join):
            on = _inline(stage.on, self.row_derives)
            self.joins.append(_join_spec(stage, on))
        elif isinstance(stage, Where):
            if stage.phase == "group":
                self.having = _combine(self.having, _inline(stage.condition, self._group_names))
            else:
                self.where = _combine(self.where, _inline(stage.condition, self.row_derives))
        elif isinstance(stage, Derive):
            if stage.phase == "group":
                for name, expr in stage.items:
                    self.group_derives.append((name, _inline(expr, self._group_names)))
            else:
                for name, expr in stage.items:
                    self.row_derives[name] = _inline(expr, self.row_derives)
        elif isinstance(stage, Group):
            self.group_keys = [(name, _inline(expr, self.row_derives)) for name, expr in stage.keys]
            self.aggregates = [(name, _inline(call, self.row_derives)) for name, call in stage.aggregates]
            self.grouped = True
        elif isinstance(stage, Select):
            self.final_select = self._select_items(stage)
        elif isinstance(stage, Sort):
            scope = self._group_names if self.grouped else self.row_derives
            self.order_by.extend(OrderItem(_inline(key.expression, scope), key.descending)
                                 for key in stage.keys)
        elif isinstance(stage, Take):
            self.limit = stage.count
        elif isinstance(stage, Skip):
            self.offset = stage.count
        elif isinstance(stage, Distinct):
            self.distinct = True
        else:  # pragma: no cover - defensive
            raise PipelineError(f"cannot lower stage {stage.keyword!r}")

    def _select_items(self, stage: Select) -> list[SelectItem]:
        if stage.star:
            return self._default_items()
        scope = self._group_names if self.grouped else self.row_derives
        items: list[SelectItem] = []
        for item in stage.items:
            expr = item.expression
            if self.grouped and item.alias is None and isinstance(expr, Identifier) \
                    and expr.name in self._group_names:
                items.append(SelectItem(Identifier(expr.name)))
            else:
                items.append(SelectItem(_inline(expr, scope), item.alias))
        return items

    def _default_items(self) -> list[SelectItem]:
        if self.grouped:
            items = [SelectItem(expr, name) for name, expr in self.group_keys]
            items.extend(SelectItem(expr, name) for name, expr in self.aggregates)
            items.extend(SelectItem(expr, name) for name, expr in self.group_derives)
            return items
        if self.row_derives:
            return [SelectItem(Identifier(name), name) for name in self.row_derives]
        return [SelectItem(Wildcard())]

    def build(self) -> Query:
        if self.source is None:  # pragma: no cover - parser guarantees a from stage
            raise PipelineError("a pipeline must start with a from stage")
        select = self.final_select if self.final_select is not None else self._default_items()
        if not select:
            raise PipelineError("the pipeline produces no output columns")
        return Query(
            source=self.source,
            joins=self.joins,
            where=self.where,
            group_by=[expr for _, expr in self.group_keys],
            having=self.having,
            select=select,
            order_by=self.order_by,
            limit=self.limit,
            offset=self.offset,
            distinct=self.distinct,
        )


def _join_spec(stage: Join, on: Expr):
    from .parser import JoinSpec

    src = stage.source
    return JoinSpec(src.name, src.alias, stage.kind, on, src.subquery)


def lower_to_query(stages: list[Stage]) -> Query:
    """Collapse a stage list onto the ordered-clause Query AST."""
    if any(isinstance(stage, RawSql) for stage in stages):
        raise PipelineError(
            'a sql "..." stage cannot be lowered to the ordered engine; '
            "run the pipeline with execute_pipeline_stages or the duckdb engine")
    builder = _Builder()
    for stage in stages:
        builder.add(stage)
    return builder.build()


def parse_pipeline(text: str) -> Query:
    """Parse pipeline text and lower it to the ordered-clause Query AST."""
    return lower_to_query(parse_pipeline_stages(text))


def execute_pipeline_stages(stages: list[Stage], tables: dict[str, Table]) -> Table:
    """Execute a stage list on the in-memory engine, running sql stages via sqlite.

    The pipeline is cut into segments at every sql stage. Ordinary segments are
    lowered and executed as before; a sql segment runs the raw text against an
    in-memory sqlite database in which the previous relation is table
    __input__ (next to the original named tables).
    """
    if not any(isinstance(stage, RawSql) for stage in stages):
        return execute(lower_to_query(stages), tables)

    current: Table | None = None
    segment: list[Stage] = []
    for stage in stages:
        if isinstance(stage, RawSql):
            current = _run_segment(segment, tables, current)
            segment = []
            current = run_sql_stage(stage.text, tables, current)
        else:
            segment.append(stage)
    return _run_segment(segment, tables, current)


def _run_segment(segment: list[Stage], tables: dict[str, Table],
                 current: Table | None) -> Table:
    if not segment:
        if current is None:
            raise PipelineError("a pipeline must start with a from stage")
        return current
    if isinstance(segment[0], From):
        return execute(lower_to_query(segment), tables)
    if current is None:
        raise PipelineError("a pipeline must start with a from stage")
    sourced = [From("from", segment[0].line, FromSpec("__input__")), *segment]
    return execute(lower_to_query(sourced), {**tables, "__input__": current})
