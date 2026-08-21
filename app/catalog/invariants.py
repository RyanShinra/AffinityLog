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

  * ORPHANED BARE ROW — a heading with both a NULL-variant_kind row and INTERFACE rows. Tier one
    qualifies, so the bare row can never be returned. It is dead data that looks live.

    INTERFACE specifically, and this was wrong here until measured. An earlier version of this
    file flagged a bare row beside ANY qualified kind, on the reasoning that tier one "always
    qualifies". It does not: it qualifies only when INTERFACE is among the heading's kinds, because
    that is the only axis whose variant is absent from the key string. With any other kind the
    resolver uses what `decompose()` returned, and the bare row resolves correctly.

    The over-broad version rejected a shape this project has already written down as its next
    catalog addition. `seed/catalog.json` says of `fastdpe.SFvCSP`: "A raw and a '-transformed'
    form exist; when the transformed variant is seeded it uses VariantKind.TRANSFORM" — a bare row
    beside a TRANSFORM row, one heading. Built and measured: the old check flagged it
    ("has 1 variant-less row(s) beside ['TRANSFORM'], which can never be reached") while the lookup
    resolved `fastdpe.SFvCSP` to the raw row without difficulty. A write-time check that refuses a
    legitimate, documented shape is worse than no check, because it blocks the work rather than the
    error.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import orm as db


class HeadingViolation(NamedTuple):
    """One `(module, column_key)` heading whose catalog rows break the functional dependency."""

    module: str
    column_key: str
    variant_kinds: tuple[str, ...]  # the distinct non-NULL axes found, sorted
    bare_rows: int  # rows with variant_kind IS NULL under the same heading

    def describe(self) -> str:
        """Every reason this heading was flagged, not just the first.

        The two HAVING arms are independent, so one heading can trip both. Reporting only the
        axes would send the operator round twice: fix the axes, re-run, fail again on the orphan
        that was in the tuple the whole time.
        """
        reasons: list[str] = []
        if len(self.variant_kinds) > 1:
            reasons.append(f"catalogued along {len(self.variant_kinds)} axes {list(self.variant_kinds)}")
        if self.bare_rows and db.VariantKind.INTERFACE.name in self.variant_kinds:
            reasons.append(f"has {self.bare_rows} variant-less row(s) beside INTERFACE rows, which can never be reached")
        return f"{self.module}.{self.column_key}: {' and '.join(reasons)}"


# `count(DISTINCT variant_kind)` ignores NULLs, which is why the bare-row case needs its own
# FILTER clause rather than falling out of the same count. Grouping is on module_id + column_key —
# the heading — because that is the granularity the resolver's tier one asks about.
#
# The second arm tests for INTERFACE specifically, not for "any qualified kind". See the docstring:
# only INTERFACE makes tier one qualify, so only INTERFACE can strand a bare row. `:interface_kind`
# is bound from `VariantKind.INTERFACE.name` rather than written as a literal, so a rename of the
# enum member cannot leave this SQL silently testing for a value that no longer exists.
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
        OR (    count(*) FILTER (WHERE me.variant_kind IS NULL)                     > 0
            AND count(*) FILTER (WHERE me.variant_kind::text = :interface_kind)     > 0 )
    ORDER BY    mo.name, me.column_key
    """)


async def find_heading_violations(session: AsyncSession) -> list[HeadingViolation]:
    """Every heading breaking `(module, column_key) -> variant_kind`. Empty list means clean.

    Call INSIDE the seeding transaction and before `commit()`: uncommitted rows are visible to
    their own transaction, so raising on a non-empty result rolls the bad seed back instead of
    reporting it after the fact.
    """
    result = await session.execute(_VIOLATIONS_SQL, {"interface_kind": db.VariantKind.INTERFACE.name})
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
        f"{source} refused to commit: the catalog breaks (module, column_key) -> variant_kind, "
        f"which the ScoreEntry lookup depends on and the schema does not enforce.\n"
        f"{detail}\n"
        f"This run's own writes were rolled back. The offending rows may PREDATE it — the check "
        f"reads the whole `metrics` table, not just what {source} wrote — so look at the headings "
        f"named above before assuming this run introduced them.\n"
        f"See app/catalog/invariants.py for why this is checked at write time."
    )
