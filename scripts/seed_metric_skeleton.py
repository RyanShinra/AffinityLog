#!/usr/bin/env python3
"""Register every score key in the corpus as a metric, curated or not. Idempotent.

WHY
---
`seed_catalog.py` curates 22 of the 138 metric identities the corpus contains (as 28 rows — the three
interface-dependent columns are each split into three INTERFACE variants). That leaves 116 keys that
exist in `candidates.scores` with no row in `metrics` — and a GraphQL `ScoreEntry` resolver would
then have to special-case "this key has no catalog entry" for most of the data.

This registers the remainder as *uncurated* rows, so the resolver always finds one and "we have not
curated this yet" becomes a visible state rather than an absent row. Uncurated rows are honest about
being uncurated:

    display_name  = the raw column_key ("t65_binary"), not an invented friendly name
    direction     = NEUTRAL, so nothing can sort by a metric whose polarity we never established
    notes         = NULL
    unit          = NULL

TWO RULES THAT KEEP THIS SAFE
-----------------------------
1. **`ON CONFLICT DO NOTHING`, never DO UPDATE.** The curated rows carry hand-written display names
   and warnings. A DO UPDATE here would overwrite "Heavy-light pairing confidence" with
   "protein_iptm" on the next run. Skeleton rows fill gaps; they must never clobber curation.

2. **Skip a (module, column_key) that has ANY curated row**, not just an exactly-matching identity.
   This is the subtle one. The extractor reports `boltz2.protein_iptm` once, with no variant. The
   catalog stores it as THREE rows with variant_kind=INTERFACE. Those differ in the unique key, so a
   naive insert would not conflict — it would add a fourth, variant-less, meaningless row alongside
   the three that carry the actual meaning.

MODULES
-------
Seven modules appear in the corpus but not in the curated catalog (cdr_indices, rfantibody, hdbscan,
mmseqs, ...). They get auto-registered so their metrics have a parent. `module_type` is NOT NULL and
we have not curated these, so they are recorded as SCORE with a description saying plainly that the
classification is a placeholder — rfantibody, for one, is really a design module.

    python scripts/seed_metric_skeleton.py            # register
    python scripts/seed_metric_skeleton.py --dry-run  # report what would be added
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identifiers import ColumnKey, ModuleName
from app.catalog.invariants import find_heading_violations, raise_on_heading_violations
from app.database import AsyncSessionLocal
from app.models import orm as db

# The repo root, not `scripts/` — so the sibling below is imported as `scripts.<name>` and cannot
# also be loaded as a bare top-level module. `scripts/` has an `__init__.py`, so a file reached both
# ways becomes TWO module objects with two copies of every class, and `isinstance` across them is
# False. That split is also what made a root-level `mypy .` refuse to run before the package marker
# existed. Needed because `scripts` is deliberately NOT installed — `[tool.setuptools.packages.find]`
# is `include = ["app*"]` — so running this file directly puts `scripts/` on the path, not the root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.extract_score_keys import MetricKey, collect  # noqa: E402  (needs the sys.path line above)

_PLACEHOLDER_MODULE_TYPE: Final[str] = "SCORE"
_PLACEHOLDER_DESCRIPTION: Final[str] = (
    "Auto-registered from score keys found in the corpus. Not curated: module_type is a placeholder " "and functions are unset."
)


def infer_value_type(values: list[str]) -> db.MetricValueType:
    """Infer a MetricValueType from the values a key actually holds in the corpus.

    Everything in `candidates.scores` is stored as text (the loader does not coerce), so the type
    has to be recovered from the strings. Order matters — "0" and "1" parse as INT, so the explicit
    true/false check has to come first or booleans would never be detected.

    Observed shapes this has to handle, from the real corpus:
        "0.7925468683242798"  -> FLOAT
        "4", "-1"             -> INT      (including t*_binary, which is 0/1, honestly an int)
        "mesophilic"          -> CATEGORICAL
        "-", "<40", "pdb"     -> CATEGORICAL
        '{"123": {"H2": 2}}'  -> CATEGORICAL (JSON text; MetricValueType has no JSON member)

    Falls back to CATEGORICAL, which is the safe direction: a consumer that treats a number as a
    label renders it correctly, while one that treats a label as a number raises.

    Returns the ENUM, not its name. It returned bare strings until stage 5, which is the same hazard
    `variant_kind` had before stage 3c: `"CATEGORCAL"` typechecks, matches no member, and fails at
    `CAST(:value_type AS metricvaluetype)` against a real database rather than here. `values` stays
    `list[str]` because those genuinely are raw text — the loader does not coerce.
    """
    present = [v for v in values if v is not None and v.strip() != ""]
    if not present:
        return db.MetricValueType.CATEGORICAL
    if all(v.strip().lower() in ("true", "false") for v in present):
        return db.MetricValueType.BOOL
    try:
        for v in present:
            int(v.strip())
        return db.MetricValueType.INT
    except ValueError:
        pass
    try:
        for v in present:
            float(v.strip())
        return db.MetricValueType.FLOAT
    except ValueError:
        pass
    return db.MetricValueType.CATEGORICAL


async def _values_by_key(session: AsyncSession) -> dict[str, list[str]]:
    """Every value the corpus holds, grouped by raw JSONB key. One query, ~2.8k rows at this size."""
    result = await session.execute(text("SELECT k, c.scores->>k FROM candidates c, jsonb_object_keys(c.scores) k"))
    values: dict[str, list[str]] = {}
    for key, value in result.all():
        values.setdefault(key, []).append(value)
    return values


async def _existing_module_ids(session: AsyncSession) -> dict[ModuleName, str]:
    """Module name -> id. The VALUE is a uuid string and stays one; only the key is an identifier."""
    result = await session.execute(text("SELECT name, id FROM modules"))
    return {ModuleName(name): str(mid) for name, mid in result.all()}


async def _curated_column_keys(session: AsyncSession) -> set[tuple[ModuleName, ColumnKey]]:
    """(module_name, column_key) pairs that already have at least one metric row.

    Compared at this level rather than on the full identity so that a column already stored as
    several variant rows is not joined by a spurious variant-less sibling. See rule 2 above.
    """
    result = await session.execute(
        text("SELECT mo.name, me.column_key FROM metrics me JOIN modules mo ON mo.id = me.module_id")
    )
    # THE STRING BOUNDARY IS HERE, at the row — the same shape `app/catalog/invariants.py` uses.
    # Past this line the pair is comparable to a `MetricKey`'s identity without either side
    # widening to `str` and forgetting which half is which.
    return {(ModuleName(name), ColumnKey(column_key)) for name, column_key in result.all()}


async def _register_module(session: AsyncSession, name: ModuleName) -> str:
    """Insert a placeholder module row and return its id. The id is a uuid string, not a name.

    `name` is a `ModuleName` because that is what it always was: every caller passes `m.module`
    off a `MetricKey`, which `decompose()` typed. An earlier version of this signature said `str`
    and justified it as "where a raw name enters from a CSV header" — nothing here reads a CSV,
    and the value had already been through `decompose()` two calls earlier.
    """
    created = await session.execute(
        text("""
            INSERT INTO modules (id, name, module_type, functions, description)
            VALUES (gen_random_uuid(), :name, CAST(:module_type AS moduletype), '{}', :description)
            ON CONFLICT (name) DO NOTHING
            RETURNING id
            """),
        {"name": name, "module_type": _PLACEHOLDER_MODULE_TYPE, "description": _PLACEHOLDER_DESCRIPTION},
    )
    if (row := created.first()) is not None:
        return str(row[0])
    # DO NOTHING returns no row when it collided; fetch the existing id.
    found = await session.execute(text("SELECT id FROM modules WHERE name = :name"), {"name": name})
    return str(found.scalar_one())


async def _insert_skeleton_metric(
    session: AsyncSession, module_id: str, metric: MetricKey, value_type: db.MetricValueType
) -> bool:
    result = await session.execute(
        text("""
            INSERT INTO metrics (
                id, module_id, column_key, display_name, value_type,
                direction, property_categories, variant_kind, variant, provenance
            )
            VALUES (
                gen_random_uuid(), CAST(:module_id AS uuid), :column_key, :display_name,
                CAST(:value_type AS metricvaluetype),
                CAST('NEUTRAL' AS direction), '{}',
                CAST(:variant_kind AS variantkind), :variant,
                CAST('INFERRED' AS provenance)
            )
            ON CONFLICT ON CONSTRAINT uq_metric_identity DO NOTHING
            RETURNING id
            """),
        {
            "module_id": module_id,
            "column_key": metric.column_key,
            # The raw key IS the display name. An uncurated metric should look uncurated.
            "display_name": metric.column_key,
            # Inferred from the values, not assumed. Typing everything FLOAT would mislabel the
            # text columns (protein_id, structureFile, the _export.recommendation prose) and any
            # consumer trusting value_type to parse them would raise.
            # `.name` for the same reason `variant_kind` needs it below: the bind feeds
            # CAST(... AS metricvaluetype) and Postgres enum labels ARE the member names.
            "value_type": value_type.name,
            # `.name` because the bind feeds `CAST(:variant_kind AS variantkind)`, and Postgres
            # enum labels ARE the member names. Passing the member itself raises asyncpg's
            # DataError — checked, not assumed. This is the other end of the boundary that
            # `app/catalog/invariants.py` reads back with `VariantKind[label]`.
            "variant_kind": metric.variant_kind.name if metric.variant_kind is not None else None,
            "variant": metric.variant,
        },
    )
    return result.first() is not None


async def seed(dry_run: bool = False) -> None:
    metrics = await collect()

    async with AsyncSessionLocal() as session:
        module_ids = await _existing_module_ids(session)
        curated = await _curated_column_keys(session)
        corpus_values = await _values_by_key(session)

        pending = [m for m in metrics if (m.module, m.column_key) not in curated]
        new_modules = sorted({m.module for m in pending if m.module not in module_ids})

        # A metric's type is inferred from every value across all the raw keys that collapsed into
        # it — e.g. temstapro.clash.H/.L/.T are one metric, so all three chains' values vote.
        types = {m: infer_value_type([v for key in m.raw_keys for v in corpus_values.get(key, [])]) for m in pending}

        async def apply_inserts() -> int:
            """Register the pending modules and metrics. Shared by the dry run and the real one."""
            for name in new_modules:
                module_ids[name] = await _register_module(session, name)
            registered = 0
            for metric in pending:
                if await _insert_skeleton_metric(session, module_ids[metric.module], metric, types[metric]):
                    registered += 1
            return registered

        if dry_run:
            print(f"{len(metrics)} identities in the corpus, {len(metrics) - len(pending)} already curated")
            print(f"would register {len(pending)} skeleton metrics")
            print(f"would auto-create {len(new_modules)} modules: {', '.join(new_modules) or '(none)'}")
            tally: dict[db.MetricValueType, int] = {}
            for value_type in types.values():
                tally[value_type] = tally.get(value_type, 0) + 1
            # `.name` twice, both load-bearing. Sorting: neither `Enum` nor `None` defines `__lt__`,
            # so `sorted(tally.items())` raises TypeError the first time this line has more than
            # one type to report — invisible against a fully curated corpus, where `types` is
            # empty. Printing: `f"{t}"` on an enum renders "MetricValueType.FLOAT", not "FLOAT".
            print("inferred types: " + ", ".join(f"{t.name}={n}" for t, n in sorted(tally.items(), key=lambda kv: kv[0].name)))

            # The dry run APPLIES the inserts and then does not commit. Reporting on the database
            # as it stands would answer a question nobody asked — what matters is whether the real
            # run would abort, and that depends on the rows this run would add. The session closes
            # without commit(), so the writes are discarded either way.
            await apply_inserts()
            violations = await find_heading_violations(session)
            for violation in violations:
                print(f"  ERROR: {violation.describe()}")
            print(
                f"heading invariant: {'would ABORT the real run' if violations else 'clean'} "
                f"({len(violations)} violation(s)); nothing written"
            )
            return

        inserted = await apply_inserts()

        # Before the commit — see app/catalog/invariants.py. This seeder is the likelier of the two
        # to trip it: it skips on (module, column_key) rather than on full identity, and that skip
        # is the ONLY thing stopping a bare skeleton row landing beside curated INTERFACE rows.
        await raise_on_heading_violations(session, source="seed_metric_skeleton")

        await session.commit()

    print(f"skeleton: {inserted} metrics registered, {len(pending) - inserted} already present")
    print(f"modules auto-created: {len(new_modules)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Register uncurated corpus score keys as metrics")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
