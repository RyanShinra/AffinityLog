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

DO NOT READ THAT AS "EVERY CATALOG INVARIANT BELONGS IN THIS FILE"
-----------------------------------------------------------------
It applies to the functional dependency above and to nothing else by default. A sibling invariant —
that `variant_kind` and `variant` are both set or both NULL — IS expressible as a plain CHECK, so it
lives in the schema (migration 008, `ck_metric_variant_pair`) rather than here, and this module
neither checks nor needs to check it.

That one used to matter here in a way that is easy to miss: a row with a NULL `variant_kind` and a
non-NULL `variant` was counted as a BARE row by the query below, and `bare_rows` is what suppresses
the third branch. So a single malformed row silenced the unsatisfiable-axis check for its whole
heading. The constraint makes that row unwritable, which is why no branch here reports it — the
Python version would be dead code. Before adding a check to this file, ask whether a constraint can
carry it instead.

FOUR WAYS A HEADING BREAKS THE LOOKUP
-------------------------------------
All four are silent, and only the first is about "more than one kind":

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

  * AN AXIS NOTHING CAN SUPPLY — a heading with no bare row, no INTERFACE rows, and an axis
    `decompose()` cannot read out of the key string. `decomposable_kinds_for(module, column_key)` in
    `app/catalog/keys.py` answers what the key string carries FOR THAT HEADING; tier two supplies
    only INTERFACE. So a heading qualified solely along MODE, SOURCE_MODEL, COMPONENT or TRANSFORM
    resolves to NOTHING, for every candidate, with no exception and no log. This is the check that
    guards not just "one axis per heading" but "the axis is one the lookup can satisfy" — the
    assertion that actually protects the read path.

    Per heading, and that word is load-bearing. This asked `kind in DECOMPOSABLE_VARIANT_KINDS`
    until 2026-08-23 — a flat set of kind NAMES flattened from rules that are pinned to a module
    and a column. PARAMETER is recoverable for `evoprotgrad.pseudolikelihood_ratio` and for nothing
    else, so `evoprotgrad.entropy` catalogued along PARAMETER passed clean while resolving to
    nothing: a false negative in precisely the shape this branch exists to catch.

    Note how it composes with the case above rather than contradicting it: TRANSFORM beside a bare
    row is fine (the bare row answers), TRANSFORM alone is not (nothing answers).

  * AN AXIS WITH MISSING VALUES — a heading qualified along a RESOLVABLE axis that it does not
    fully cover. The three above ask whether an axis can be resolved at all; this asks whether
    every value of it was actually catalogued. Tier two builds
    (module, column_key, 'INTERFACE', <the candidate's interface kind>), so a heading seeded with
    two of the three scoreable kinds resolves to nothing for exactly the candidates carrying the
    third, while every other candidate resolves fine. A partial failure is harder to notice than a
    total one, and all three kinds occur in the real corpus (6 / 4 / 4 of the 14 candidates).

    The expected set comes from `InterfaceKind.scoreable()` for INTERFACE and from
    `declared_variants_for(module, column_key, kind)` for everything else — the latter only askable
    since the rules table began carrying its variants as data rather than as regex capture groups.
    It takes the AXIS as well as the heading, and must: without it, a heading carrying a rule for
    one axis was measured against that rule's variants while catalogued along a different one, so
    `evoprotgrad.pseudolikelihood_ratio` with a TRANSFORM row beside a bare row was refused while
    `fastdpe.SFvCSP` in the identical shape passed.

    `NO_CHAINS_RECORDED` is excluded, which is not a new judgment: the enum docstring, the CASE
    comment in sql/candidate_summary.sql, and tests/conftest.py all already say the catalog carries
    three rows per interface-dependent column and not four.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identifiers import ColumnKey, ModuleName, VariantName
from app.catalog.interface_kind import InterfaceKind
from app.catalog.keys import declared_variants_for, decomposable_kinds_for
from app.catalog.variant_kind import VariantKind


class Heading(NamedTuple):
    """One `(module, column_key)` and the shape of its catalog rows."""

    module: ModuleName
    column_key: ColumnKey
    variant_kinds: tuple[VariantKind, ...]  # the distinct non-NULL axes found, sorted by name
    variants: tuple[VariantName, ...]  # the distinct non-NULL variant values found, sorted
    bare_rows: int  # rows with variant_kind IS NULL under the same heading

    def problems(self) -> tuple[str, ...]:
        """Every way this heading breaks the lookup. Empty means it is fine.

        Classified here rather than in the SQL's HAVING for two reasons: the third test needs a set
        imported from `app.catalog.keys`, and reporting EVERY applicable reason matters. One heading
        can trip more than one; naming only the first would send the operator round the fix-and-retry
        loop once per problem.
        """
        reasons: list[str] = []
        interface = VariantKind.INTERFACE

        if len(self.variant_kinds) > 1:
            reasons.append(f"catalogued along {len(self.variant_kinds)} axes {[k.name for k in self.variant_kinds]}")

        if self.bare_rows and interface in self.variant_kinds:
            reasons.append(f"has {self.bare_rows} variant-less row(s) beside INTERFACE rows, which can never be reached")

        # Nothing can supply the variant: no bare row to fall back to, no INTERFACE for tier two to
        # fill in, and an axis `decompose()` cannot read out of the key string.
        if not self.bare_rows and interface not in self.variant_kinds:
            # Asked PER HEADING, because that is the only answerable form of the question: the
            # rules in app/catalog/keys.py are pinned to a module AND a column, so PARAMETER is
            # recoverable for `evoprotgrad.pseudolikelihood_ratio` and nowhere else. Asking whether
            # a KIND is decomposable — which this did until 2026-08-23 — passes any heading whose
            # axis happens to be spelled PARAMETER while nothing can resolve it.
            recoverable = decomposable_kinds_for(self.module, self.column_key)
            unsatisfiable = [k.name for k in self.variant_kinds if k not in recoverable]
            if unsatisfiable:
                readable = sorted(k.name for k in recoverable) if recoverable else "nothing"
                reasons.append(
                    f"is qualified along {unsatisfiable}, which nothing can supply — not the key "
                    f"string (decompose recovers {readable} for this heading), not tier two "
                    f"(INTERFACE only), and there is no variant-less row to fall back to"
                )

        # FOURTH: a resolvable axis this heading does not fully cover. Only asked when there is
        # exactly ONE axis — two or more is already refused above, and the query aggregates variants
        # across the whole heading rather than per kind, so pairing them back up is neither possible
        # here nor needed. An empty `expected` means nothing declares what full coverage is, which is
        # not the same as being covered: that case is the third branch's to refuse, not this one's.
        if len(self.variant_kinds) == 1:
            (kind,) = self.variant_kinds
            expected = (
                InterfaceKind.scoreable() if kind == interface else declared_variants_for(self.module, self.column_key, kind)
            )
            missing = sorted(expected - set(self.variants))
            if missing:
                reasons.append(
                    f"is qualified along {kind.name} but has no row for {missing} — a candidate whose "
                    f"variant is one of those resolves to no metric, while every other candidate "
                    f"under this heading resolves fine"
                )

        return tuple(reasons)

    def describe(self) -> str:
        return f"{self.module}.{self.column_key}: {' and '.join(self.problems())}"


# `count(DISTINCT variant_kind)` ignores NULLs, which is why the bare-row case needs its own
# FILTER clause rather than falling out of the same count. Grouping is on module_id + column_key —
# the heading — because that is the granularity the resolver's tier one asks about.
#
# No HAVING: this returns EVERY heading and `Heading.problems()` decides which are broken. The
# classification needs `decomposable_kinds_for` from app.catalog.keys, which SQL cannot import,
# and at 144 rows the difference is not worth splitting the logic across two languages.
_HEADINGS_SQL = text("""
    SELECT  mo.name                                                       AS module,
            me.column_key                                                 AS column_key,
            array_agg(DISTINCT me.variant_kind::text)
                FILTER (WHERE me.variant_kind IS NOT NULL)                AS variant_kinds,
            array_agg(DISTINCT me.variant)
                FILTER (WHERE me.variant IS NOT NULL)                     AS variants,
            count(*) FILTER (WHERE me.variant_kind IS NULL)               AS bare_rows
    FROM        metrics me
    JOIN        modules mo ON mo.id = me.module_id
    GROUP BY    mo.name, me.column_key
    ORDER BY    mo.name, me.column_key
    """)


async def find_heading_violations(session: AsyncSession) -> list[Heading]:
    """Every heading the ScoreEntry lookup cannot resolve. Empty list means clean.

    Call INSIDE the seeding transaction and before `commit()`: uncommitted rows are visible to
    their own transaction, so raising on a non-empty result rolls the bad seed back instead of
    reporting it after the fact.
    """
    result = await session.execute(_HEADINGS_SQL)
    headings = [
        Heading(
            module=ModuleName(row.module),
            column_key=ColumnKey(row.column_key),
            # THE STRING BOUNDARY. `_HEADINGS_SQL` casts the enum column to text, so this is the
            # one place a label becomes a member. `VariantKind[...]` raises KeyError on a label no
            # Python member declares — which cannot happen, because
            # `tests/test_postgres_enum_labels.py` refuses a database whose type carries one.
            variant_kinds=tuple(sorted((VariantKind[label] for label in row.variant_kinds or ()), key=lambda k: k.name)),
            variants=tuple(VariantName(v) for v in sorted(row.variants or ())),
            bare_rows=row.bare_rows,
        )
        for row in result
    ]
    return [heading for heading in headings if heading.problems()]


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
