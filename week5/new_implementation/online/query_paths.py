"""WHERE paths that contribute to the final SQL result, excluding unused CTEs."""

from __future__ import annotations

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import Scope, build_scope


MAX_PATHS = 256


def boolean_paths(
    node: exp.Expression,
) -> tuple[tuple[exp.Expression, ...], ...] | None:
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Or):
        left, right = boolean_paths(node.this), boolean_paths(node.expression)
        if left is None or right is None or len(left) + len(right) > MAX_PATHS:
            return None
        return left + right
    if isinstance(node, exp.And):
        left, right = boolean_paths(node.this), boolean_paths(node.expression)
        if left is None or right is None or len(left) * len(right) > MAX_PATHS:
            return None
        return tuple(a + b for a in left for b in right)
    return ((node,),)


def _combine(
    inherited: tuple[tuple[exp.Expression, ...], ...],
    local: tuple[tuple[exp.Expression, ...], ...],
) -> tuple[tuple[exp.Expression, ...], ...] | None:
    if len(inherited) * len(local) > MAX_PATHS:
        return None
    return tuple(a + b for a in inherited for b in local)


def _paths_from_scope(
    scope: Scope,
    inherited: tuple[tuple[exp.Expression, ...], ...],
) -> tuple[tuple[exp.Expression, ...], ...] | None:
    if scope.set_operation_scopes:
        branches = []
        for child in scope.set_operation_scopes:
            paths = _paths_from_scope(child, inherited)
            if paths is None or len(branches) + len(paths) > MAX_PATHS:
                return None
            branches.extend(paths)
        return tuple(branches)

    where = scope.expression.args.get("where")
    local = boolean_paths(where.this) if where is not None else ((),)
    if local is None or (combined := _combine(inherited, local)) is None:
        return None
    paths = []
    source_scopes = set()
    for _alias, (_node, source) in scope.selected_sources.items():
        if isinstance(source, Scope):
            source_scopes.add(id(source))
            contribution = _paths_from_scope(source, combined)
        elif isinstance(source, exp.Table):
            contribution = combined
        else:
            return None
        if contribution is None or len(paths) + len(contribution) > MAX_PATHS:
            return None
        paths.extend(contribution)

    for subquery in scope.subquery_scopes:
        if id(subquery) in source_scopes:
            continue
        # A scalar subquery contributes independently of its parent's WHERE.
        contribution = _paths_from_scope(subquery, ((),))
        if contribution is None or len(paths) + len(contribution) > MAX_PATHS:
            return None
        paths.extend(contribution)
    return tuple(paths)


def contributing_where_paths(
    sql: str,
) -> tuple[tuple[exp.Expression, ...], ...] | None:
    """Return every contributing base-table path; None means cannot prove scope."""

    try:
        root = build_scope(parse_one(sql, read="postgres"))
    except (ParseError, ValueError):
        return None
    if root is None:
        return None
    return _paths_from_scope(root, ((),))


__all__ = ["boolean_paths", "contributing_where_paths"]
