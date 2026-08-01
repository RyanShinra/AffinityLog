#!/usr/bin/env python3
"""Register every score key in the corpus as a metric, curated or not. Idempotent.

WHY
---
`seed_catalog.py` curates ~20 metric identities out of the 138 the corpus contains. That leaves 100+
keys that exist in `candidates.scores` with no row in `metrics` — and a GraphQL `ScoreEntry`
resolver would then have to special-case "this key has no catalog entry" for most of the data.

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

from app.database import AsyncSessionLocal

# scripts/ is on sys.path when a script here is run directly, but not when this module is imported
# from elsewhere; make the sibling import work either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_score_keys import MetricKey, collect  # noqa: E402  (needs the sys.path line above)

_PLACEHOLDER_MODULE_TYPE: Final[str] = "SCORE"
_PLACEHOLDER_DESCRIPTION: Final[str] = (
    "Auto-registered from score keys found in the corpus. Not curated: module_type is a placeholder " "and functions are unset."
)


async def _existing_module_ids(session: AsyncSession) -> dict[str, str]:
    result = await session.execute(text("SELECT name, id FROM modules"))
    return {name: str(mid) for name, mid in result.all()}


async def _curated_column_keys(session: AsyncSession) -> set[tuple[str, str]]:
    """(module_name, column_key) pairs that already have at least one metric row.

    Compared at this level rather than on the full identity so that a column already stored as
    several variant rows is not joined by a spurious variant-less sibling. See rule 2 above.
    """
    result = await session.execute(
        text("SELECT mo.name, me.column_key FROM metrics me JOIN modules mo ON mo.id = me.module_id")
    )
    return {(name, column_key) for name, column_key in result.all()}


async def _register_module(session: AsyncSession, name: str) -> str:
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


async def _insert_skeleton_metric(session: AsyncSession, module_id: str, metric: MetricKey) -> bool:
    result = await session.execute(
        text("""
            INSERT INTO metrics (
                id, module_id, column_key, display_name, value_type,
                direction, property_categories, variant_kind, variant, provenance
            )
            VALUES (
                gen_random_uuid(), CAST(:module_id AS uuid), :column_key, :display_name,
                CAST('FLOAT' AS metricvaluetype),
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
            "variant_kind": metric.variant_kind,
            "variant": metric.variant,
        },
    )
    return result.first() is not None


async def seed(dry_run: bool = False) -> None:
    metrics = await collect()

    async with AsyncSessionLocal() as session:
        module_ids = await _existing_module_ids(session)
        curated = await _curated_column_keys(session)

        pending = [m for m in metrics if (m.module, m.column_key) not in curated]
        new_modules = sorted({m.module for m in pending if m.module not in module_ids})

        if dry_run:
            print(f"{len(metrics)} identities in the corpus, {len(metrics) - len(pending)} already curated")
            print(f"would register {len(pending)} skeleton metrics")
            print(f"would auto-create {len(new_modules)} modules: {', '.join(new_modules) or '(none)'}")
            return

        for name in new_modules:
            module_ids[name] = await _register_module(session, name)

        inserted = 0
        for metric in pending:
            if await _insert_skeleton_metric(session, module_ids[metric.module], metric):
                inserted += 1
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
