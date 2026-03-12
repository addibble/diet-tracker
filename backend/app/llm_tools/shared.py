"""Shared utilities for the table-driven LLM tool system.

Provides fuzzy matching, SQL filter application, and standard response
builders used by all get_<table> / set_<table> handlers.
"""

from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlmodel import Session, col, select

# ── Fuzzy matching ────────────────────────────────────────────────────


def fuzzy_score(query: str, candidate: str) -> float:
    """Score how well *query* matches *candidate*. Returns 0.0–1.0."""
    q = query.lower().strip()
    c = candidate.lower().strip()
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    # Substring containment gets a high base score
    if q in c:
        return 0.85 + 0.10 * (len(q) / len(c))
    if c in q:
        return 0.85 + 0.10 * (len(c) / len(q))
    return SequenceMatcher(None, q, c).ratio()


def fuzzy_best(
    records: list,
    field: str,
    query: str,
    *,
    min_score: float = 0.60,
) -> list[tuple[Any, float]]:
    """Score *records* by fuzzy match on *field*. Returns (record, score)."""
    scored = []
    for r in records:
        val = getattr(r, field, None) if not isinstance(r, dict) else r.get(field)
        if val is None:
            continue
        sc = fuzzy_score(query, str(val))
        if sc >= min_score:
            scored.append((r, sc))
    scored.sort(key=lambda x: -x[1])
    return scored


# ── Filter helpers ────────────────────────────────────────────────────


def apply_filters(stmt, model_class, filters: dict | None, fuzzy_fields=None):
    """Apply filter predicates to a SQLAlchemy *select* statement.

    SQL-translatable operators (eq, in, gte, lte, gt, lt, contains,
    is_null) are pushed into WHERE clauses. Fuzzy filters are collected
    and returned for post-query scoring in Python.

    Returns ``(stmt, fuzzy_specs)`` where *fuzzy_specs* is a list of
    ``(field_name, query_string)`` tuples.
    """
    fuzzy_fields = set(fuzzy_fields or [])
    fuzzy_specs: list[tuple[str, str]] = []
    if not filters:
        return stmt, fuzzy_specs

    for field_name, condition in filters.items():
        if not hasattr(model_class, field_name):
            continue
        col_attr = getattr(model_class, field_name)

        if isinstance(condition, dict):
            for op, value in condition.items():
                if op == "eq":
                    stmt = stmt.where(col_attr == value)
                elif op == "in":
                    stmt = stmt.where(col(col_attr).in_(value))
                elif op == "gte":
                    stmt = stmt.where(col_attr >= value)
                elif op == "lte":
                    stmt = stmt.where(col_attr <= value)
                elif op == "gt":
                    stmt = stmt.where(col_attr > value)
                elif op == "lt":
                    stmt = stmt.where(col_attr < value)
                elif op == "contains":
                    stmt = stmt.where(
                        col(col_attr).contains(value)
                    )
                elif op == "is_null":
                    if value:
                        stmt = stmt.where(col_attr.is_(None))
                    else:
                        stmt = stmt.where(col_attr.isnot(None))
                elif op == "fuzzy" and field_name in fuzzy_fields:
                    fuzzy_specs.append((field_name, value))
        else:
            # Bare value → eq shorthand
            stmt = stmt.where(col_attr == condition)

    return stmt, fuzzy_specs


def apply_fuzzy_post_filter(
    records: list,
    fuzzy_specs: list[tuple[str, str]],
    min_score: float = 0.60,
) -> tuple[list, list[dict]]:
    """Post-filter records by fuzzy match specs after SQL query.

    Returns ``(filtered_records, match_info_list)``.
    """
    if not fuzzy_specs:
        return records, []

    results: list = []
    match_info: list[dict] = []
    for rec in records:
        best = 0.0
        matched_on: list[str] = []
        for fname, query in fuzzy_specs:
            val = getattr(rec, fname, None)
            if val is None:
                continue
            sc = fuzzy_score(query, str(val))
            if sc >= min_score:
                matched_on.append(fname)
                best = max(best, sc)
        if matched_on:
            results.append(rec)
            match_info.append({
                "record_id": rec.id,
                "matched_on": matched_on,
                "score": round(best, 3),
            })

    # Sort by descending score
    pairs = sorted(
        zip(results, match_info), key=lambda x: -x[1]["score"]
    )
    if pairs:
        results, match_info = [list(t) for t in zip(*pairs)]
    return results, match_info


def apply_sort(stmt, model_class, sort_spec: list[dict] | None):
    """Apply ``[{"field": ..., "direction": "asc"|"desc"}]`` to *stmt*."""
    if not sort_spec:
        return stmt
    for s in sort_spec:
        field = s.get("field")
        direction = s.get("direction", "asc")
        if hasattr(model_class, field):
            c = getattr(model_class, field)
            if direction == "desc":
                stmt = stmt.order_by(col(c).desc())
            else:
                stmt = stmt.order_by(c)
    return stmt


# ── Resolve a match spec to records ──────────────────────────────────


def coerce_match_spec(change: dict, *, op: str = "") -> "dict | None":
    """Return the match spec from a setter change dict.

    Normalises several forms the LLM may use instead of the canonical
    ``"match": {...}`` sub-object:

    * ``"where": {...}``  — accepted as an alias for ``"match"``
    * ``"id": N``         — promoted to ``{"id": {"eq": N}}``
    * ``"name": "..."``   — promoted to ``{"name": {"fuzzy": ...}}``
                            (only for non-create operations)
    """
    match_spec = change.get("match") or change.get("where")
    if match_spec is not None:
        return match_spec
    if change.get("id") is not None:
        return {"id": {"eq": change["id"]}}
    if change.get("name") is not None and op != "create":
        return {"name": {"fuzzy": change["name"]}}
    return None


def resolve_match(
    session: Session,
    model_class,
    match_spec: dict | None,
    *,
    fuzzy_fields: list[str] | None = None,
    min_score: float = 0.60,
    allow_multiple: bool = False,
) -> tuple[list, list[dict], str | None]:
    """Resolve a setter ``match`` clause to database records.

    Returns ``(records, match_info, error_message)``.
    ``error_message`` is *None* on success.
    """
    if not match_spec:
        return [], [], "No match criteria provided"

    stmt = select(model_class)
    stmt, fuzzy_specs = apply_filters(
        stmt, model_class, match_spec, fuzzy_fields=fuzzy_fields
    )
    records = list(session.exec(stmt).all())

    match_info: list[dict] = []
    if fuzzy_specs:
        records, match_info = apply_fuzzy_post_filter(
            records, fuzzy_specs, min_score=min_score
        )

    if not records:
        return [], [], "No matching records found"

    if not allow_multiple and len(records) > 1:
        return (
            records[:1],
            match_info[:1] if match_info else [],
            None,
        )

    return records, match_info, None


# ── Response builders ─────────────────────────────────────────────────


def getter_response(
    table: str,
    matches: list[dict],
    *,
    filters_applied: dict | None = None,
    match_info: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Standard getter response envelope."""
    r: dict[str, Any] = {
        "table": table,
        "count": len(matches),
        "matches": matches,
    }
    if filters_applied:
        r["filters_applied"] = filters_applied
    if match_info:
        r["match_info"] = match_info
    if warnings:
        r["warnings"] = warnings
    return r


def setter_response(
    table: str,
    operation: str,
    matches: list[dict],
    *,
    matched_count: int = 0,
    changed_count: int = 0,
    created_count: int = 0,
    deleted_count: int = 0,
    warnings: list[str] | None = None,
) -> dict:
    """Standard setter response envelope."""
    r: dict[str, Any] = {
        "table": table,
        "operation": operation,
        "matched_count": matched_count,
        "changed_count": changed_count,
        "created_count": created_count,
        "deleted_count": deleted_count,
        "matches": matches,
    }
    if warnings:
        r["warnings"] = warnings
    return r


def error_response(
    table: str,
    message: str,
    *,
    details: dict | None = None,
) -> dict:
    """Standard error response."""
    r: dict[str, Any] = {"table": table, "error": message}
    if details:
        r["details"] = details
    return r


# ── Small helpers ─────────────────────────────────────────────────────


def parse_date_val(s: str | date) -> date:
    """Parse YYYY-MM-DD string or pass through a date."""
    if isinstance(s, date):
        return s
    return date.fromisoformat(s)


def utcnow() -> datetime:
    return datetime.now(UTC)


def record_to_dict(rec, *, extra: dict | None = None) -> dict:
    """Convert a SQLModel record to a plain dict, optionally merging *extra*."""
    d = {}
    for key in rec.__class__.__table__.columns.keys():
        val = getattr(rec, key)
        if isinstance(val, (date, datetime)):
            val = val.isoformat()
        d[key] = val
    if extra:
        d.update(extra)
    return d
