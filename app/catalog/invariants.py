"""Catalog invariants the database schema cannot express, checked where the catalog is WRITTEN.

WHY THIS EXISTS, AND WHY IT IS NOT IN THE RESOLVER
--------------------------------------------------
`metrics` is unique on `(module_id, column_key, variant_kind, variant)`, which means the database
happily accepts BOTH of these rows:

    (boltz2, foo, PARAMETER, 'esm')
    (boltz2, foo, INTERFACE, 'antibody-target complex')

They differ in `variant_kind`, so they do not collide. But the two-tier lookup in
`app/graphql/context.py` relies on an invariant the constraint never states — a functional
dependency:

    (module_id, column_key)  ->  variant_kind

Break it and `ScoreEntry` resolution goes wrong silently. `decompose()` would recover
`PARAMETER='esm'` from the key string, the resolver would see INTERFACE among the heading's kinds
and rebuild the identity around the candidate's interface kind instead, and the `esm` would be
discarded. No exception; just the wrong meaning, or none.

The tempting fix is to raise while building the per-request catalog. That was rejected twice over.
It is the wrong layer — the resolver only READS — and it fails at the worst possible moment, taking
down every GraphQL request for all 144 metrics because one row was miscatalogued. A check here runs
when the offending row is written, names it, and rolls the seed back before it lands.

WHAT WOULD MAKE THIS FILE UNNECESSARY
-------------------------------------
Postgres cannot express a functional dependency with UNIQUE or CHECK, but it can with structure:
lift the axis into a parent keyed by the heading, so a second axis is unrepresentable rather than
merely discouraged. See `docs/metric-heading-normalization.md`. Until that migration exists, this is
the enforcement point.

TWO VIOLATIONS, NOT ONE
-----------------------
Both are silent, and only the first is about "more than one kind":

  * MIXED AXES — one heading catalogued along two different axes. No identity can name both,
    because a metric row carries a single (variant_kind, variant) pair; the cross product has
    nowhere to live.

  * ORPHANED BARE ROW — a heading with both a NULL-variant_kind row and qualified rows. Tier one
    sees the qualified kind and ALWAYS qualifies, so the bare row can never be returned. It is
    dead data that looks live.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class HeadingViolation(NamedTuple):
    """One `(module, column_key)` heading whose catalog rows break the functional dependency."""

    module: str
    column_key: str
    variant_kinds: tuple[str, ...]  # the distinct non-NULL axes found, sorted
    bare_rows: int  # rows with variant_kind IS NULL under the same heading

    def describe(self) -> str:
        if len(self.variant_kinds) > 1:
            reason = f"catalogued along {len(self.variant_kinds)} axes {list(self.variant_kinds)}"
        else:
            reason = f"has {self.bare_rows} variant-less row(s) beside {list(self.variant_kinds)}, which can never be reached"
        return f"{self.module}.{self.column_key}: {reason}"


# `count(DISTINCT variant_kind)` ignores NULLs, which is why the bare-row case needs its own
# FILTER clause rather than falling out of the same count. Grouping is on module_id + column_key —
# the heading — because that is the granularity the resolver's tier one asks about.
_VIOLATIONS_SQL = text("""
    SELECT  mo.name                                                       AS module,
            me.column_key                                                 AS column_key,
            array_agg(DISTINCT me.variant_kind::text)
                FILTER (WHERE me.variant_kind IS NOT NULL)                AS variant_kinds,
            count(*) FILTER (WHERE me.variant_kind IS NULL)               AS bare_rows
    FROM        metrics me
    JOIN        modules mo ON mo.id = me.module_id
    GROUP BY    mo.name, me.column_key
    HAVING      count(DISTINCT me.variant_kind) > 1
        OR (    count(*) FILTER (WHERE me.variant_kind IS NULL)     > 0
            AND count(*) FILTER (WHERE me.variant_kind IS NOT NULL) > 0 )
    ORDER BY    mo.name, me.column_key
    """)


async def find_heading_violations(session: AsyncSession) -> list[HeadingViolation]:
    """Every heading breaking `(module, column_key) -> variant_kind`. Empty list means clean.

    Call INSIDE the seeding transaction and before `commit()`: uncommitted rows are visible to
    their own transaction, so raising on a non-empty result rolls the bad seed back instead of
    reporting it after the fact.
    """
    result = await session.execute(_VIOLATIONS_SQL)
    return [
        HeadingViolation(
            module=row.module,
            column_key=row.column_key,
            variant_kinds=tuple(sorted(row.variant_kinds or ())),
            bare_rows=row.bare_rows,
        )
        for row in result
    ]


async def raise_on_heading_violations(session: AsyncSession, *, source: str) -> None:
    """`find_heading_violations`, but refuse to continue. `source` names the seeder for the message."""
    violations = await find_heading_violations(session)
    if not violations:
        return

    detail = "\n".join(f"  - {v.describe()}" for v in violations)
    raise ValueError(
        f"{source}: the catalog would break (module, column_key) -> variant_kind, which the "
        f"ScoreEntry lookup depends on and the schema does not enforce.\n"
        f"{detail}\n"
        f"Nothing was committed. See app/catalog/invariants.py for why this is checked here."
    )
