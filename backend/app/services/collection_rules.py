"""A small rule language for smart collections: a flat list of `field:value`
clauses joined by a single boolean operator (all AND or all OR — no mixing,
no parens, no nesting). Kept deliberately minimal — this whitelists a fixed
set of fields and hand-rolls a tiny tokenizer rather than pulling in a
grammar library, since the supported shape is too small to justify one.

Examples: ``series:Discworld``, ``genre:Fantasy AND rating>=4``,
``mood:"cozy" OR mood:"hopeful"``, ``genre:Fantasy AND -mood:dark``.
"""

import re
from dataclasses import dataclass

from sqlalchemy import Select, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Author, Book, Series

# Scalar fields compared against a plain DB column (substring, case-insensitive).
_TEXT_FIELDS = {"title", "author", "series"}
# Scalar fields inside Book.hardcover_json.meta, compared with =, and for
# rating/published also >=, <=, >, <.
_JSON_SCALAR_FIELDS = {
    "category": "$.meta.category",
    "literary_type": "$.meta.literaryType",
    "rating": "$.meta.rating",
    "published": "$.meta.published",
}
_NUMERIC_FIELDS = {"rating", "published"}
# List fields inside Book.hardcover_json.meta, checked for membership.
_JSON_LIST_FIELDS = {
    "genre": "$.meta.genres",
    "mood": "$.meta.moods",
}
_LANGUAGE_FIELD = "language"

ALL_FIELDS = _TEXT_FIELDS | set(_JSON_SCALAR_FIELDS) | set(_JSON_LIST_FIELDS) | {_LANGUAGE_FIELD}

_COMPARATORS = (">=", "<=", "!=", ">", "<", "=", ":")
_CLAUSE_RE = re.compile(
    r"""^(?P<negate>-)?
        (?P<field>[a-z_]+)
        (?P<op>""" + "|".join(re.escape(c) for c in _COMPARATORS) + r""")
        (?P<value>.+)$""",
    re.VERBOSE,
)


class CollectionRuleError(ValueError):
    """A rule string is invalid — unknown field, bad operator, empty value, or
    mixed AND/OR. Message is shown verbatim in the admin UI."""


@dataclass(frozen=True)
class RuleClause:
    field: str
    op: str  # ":" (contains/eq), "=", "!=", ">=", "<=", ">", "<"
    value: str
    negate: bool = False


@dataclass(frozen=True)
class ParsedRule:
    clauses: tuple[RuleClause, ...]
    combinator: str  # "AND" or "OR"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _split_clauses(rule: str) -> tuple[list[str], str]:
    """Split on whichever of AND/OR appears — raises if both appear."""
    has_and = bool(re.search(r"\bAND\b", rule))
    has_or = bool(re.search(r"\bOR\b", rule))
    if has_and and has_or:
        raise CollectionRuleError("A rule can use AND or OR, but not both — no mixing in one rule.")
    combinator = "OR" if has_or else "AND"
    parts = re.split(r"\bAND\b" if has_and else r"\bOR\b", rule)
    return [p.strip() for p in parts if p.strip()], combinator


def parse(rule: str) -> ParsedRule:
    if not rule or not rule.strip():
        raise CollectionRuleError("Rule can't be empty.")

    raw_clauses, combinator = _split_clauses(rule.strip())
    if not raw_clauses:
        raise CollectionRuleError("Rule can't be empty.")

    clauses: list[RuleClause] = []
    for raw in raw_clauses:
        match = _CLAUSE_RE.match(raw)
        if not match:
            raise CollectionRuleError(f"Could not parse clause {raw!r}. Expected field:value.")
        field = match.group("field")
        if field not in ALL_FIELDS:
            valid = ", ".join(sorted(ALL_FIELDS))
            raise CollectionRuleError(f"{field!r} is not a valid field. Valid fields: {valid}.")
        op = match.group("op")
        value = _unquote(match.group("value"))
        if not value:
            raise CollectionRuleError(f"Clause {raw!r} has an empty value.")
        if op not in (":", "=") and field not in _NUMERIC_FIELDS:
            raise CollectionRuleError(f"{field!r} does not support the {op!r} operator.")
        if field in _NUMERIC_FIELDS:
            try:
                float(value)
            except ValueError as exc:
                raise CollectionRuleError(f"{value!r} is not a valid number for {field!r}.") from exc
        clauses.append(RuleClause(field=field, op=op, value=value, negate=bool(match.group("negate"))))

    return ParsedRule(clauses=tuple(clauses), combinator=combinator)


def _contains_or_eq(column_expr, op: str, value: str):
    """``:`` is substring, ``=`` is exact — both case-insensitive. Only these
    two operators reach here; ``parse()`` rejects any other op for a field
    that isn't in ``_NUMERIC_FIELDS``."""
    if op == ":":
        return func.lower(column_expr).contains(value.lower())
    return func.lower(column_expr) == value.lower()


def _numeric_condition(path: str, op: str, value: str):
    extracted = func.json_extract(Book.hardcover_json, path)
    number = float(value)
    if op in (":", "="):
        return extracted == number
    if op == "!=":
        return extracted != number
    if op == ">=":
        return extracted >= number
    if op == "<=":
        return extracted <= number
    if op == ">":
        return extracted > number
    if op == "<":
        return extracted < number
    raise CollectionRuleError(f"Unsupported operator {op!r}.")


def _list_membership(path: str, value: str):
    each = func.json_each(Book.hardcover_json, path).table_valued("value")
    return select(literal(1)).select_from(each).where(func.lower(each.c.value) == value.lower()).exists()


_TEXT_COLUMNS = {"title": Book.canonical_title, "author": Author.name, "series": Series.name}


def _clause_condition(clause: RuleClause):
    if clause.field in _TEXT_FIELDS:
        condition = _contains_or_eq(_TEXT_COLUMNS[clause.field], clause.op, clause.value)
    elif clause.field in _JSON_SCALAR_FIELDS:
        path = _JSON_SCALAR_FIELDS[clause.field]
        if clause.field in _NUMERIC_FIELDS:
            condition = _numeric_condition(path, clause.op, clause.value)
        else:
            condition = _contains_or_eq(func.json_extract(Book.hardcover_json, path), clause.op, clause.value)
    elif clause.field in _JSON_LIST_FIELDS:
        condition = _list_membership(_JSON_LIST_FIELDS[clause.field], clause.value)
    elif clause.field == _LANGUAGE_FIELD:
        condition = _contains_or_eq(Book.language, clause.op, clause.value)
    else:  # pragma: no cover - unreachable, field is validated in parse()
        raise CollectionRuleError(f"Unknown field {clause.field!r}.")
    return ~condition if clause.negate else condition


def build_query(rule: ParsedRule) -> Select:
    """A SELECT of Book, joined to Author/Series only when a clause needs
    them. Caller adds any further filtering (e.g. organised files only)."""
    stmt = select(Book)
    needs_author = any(c.field == "author" for c in rule.clauses)
    needs_series = any(c.field == "series" for c in rule.clauses)
    if needs_author:
        stmt = stmt.outerjoin(Author, Author.id == Book.author_id)
    if needs_series:
        stmt = stmt.outerjoin(Series, Series.id == Book.series_id)

    conditions = [_clause_condition(c) for c in rule.clauses]
    if rule.combinator == "AND":
        for condition in conditions:
            stmt = stmt.where(condition)
    else:
        stmt = stmt.where(or_(*conditions))
    return stmt.distinct()


async def resolve(session: AsyncSession, rule: ParsedRule) -> list[Book]:
    result = await session.execute(build_query(rule))
    return list(result.scalars().unique().all())
