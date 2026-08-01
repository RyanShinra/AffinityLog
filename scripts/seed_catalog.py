#!/usr/bin/env python3
"""Load seed/catalog.json into the concepts / modules / metrics tables. Idempotent.

WHAT THIS IS
------------
The catalog is *reference data*, not experiment data: what each score key MEANS. It is hand-curated
(see seed/catalog.json) and re-seeded whenever that file changes, so this script must be safe to run
any number of times — never duplicating a row, always converging on what the JSON says.

HOW IDEMPOTENCE WORKS
---------------------
Postgres `INSERT ... ON CONFLICT (natural key) DO UPDATE`. Each table has a natural key that is
stable across re-seeds, so a second run UPDATEs the row it inserted the first time:

    concepts   -> name
    modules    -> name
    metrics    -> uq_metric_identity (module_id, column_key, variant_kind, variant)

The metrics constraint is declared `UNIQUE NULLS NOT DISTINCT`, and that is doing real work here.
Most metrics have NO variant, so variant_kind and variant are NULL. Under Postgres's default rule
NULL != NULL, which means two identical variant-less rows would NOT collide — every re-run would
insert another duplicate. NULLS NOT DISTINCT makes those NULLs compare equal, so ON CONFLICT
actually matches and the re-run updates instead. Without it this script would silently multiply
rows on every run.

FK RESOLUTION
-------------
The JSON refers to parents by NAME ("concept": "humanness"), because a hand-edited file cannot
contain UUIDs. So we build the graph bottom-up in dependency order, keeping a name -> id map from
each pass and using it to resolve the next:

    concepts  -> {name: id}
    modules   -> {name: id}
    metrics   (resolves module_id and concept_id from those two maps)

`RETURNING id` on the upsert is what gives us the id whether the row was inserted or updated.

    python scripts/seed_catalog.py            # seed / re-seed
    python scripts/seed_catalog.py --dry-run  # parse and report, touch nothing
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_CATALOG: Final[Path] = _REPO_ROOT / "seed" / "catalog.json"


async def _upsert_concepts(session: AsyncSession, rows: list[dict[str, Any]]) -> dict[str, str]:
    """Upsert concepts, returning {name: id} for the metrics pass."""
    ids: dict[str, str] = {}
    for row in rows:
        result = await session.execute(
            text("""
                INSERT INTO concepts (id, name, label, description)
                VALUES (gen_random_uuid(), :name, :label, :description)
                ON CONFLICT (name) DO UPDATE
                    SET label = EXCLUDED.label,
                        description = EXCLUDED.description
                RETURNING id
                """),
            {"name": row["name"], "label": row["label"], "description": row.get("description")},
        )
        ids[row["name"]] = str(result.scalar_one())
    return ids


async def _upsert_modules(session: AsyncSession, rows: list[dict[str, Any]]) -> dict[str, str]:
    """Upsert modules, returning {name: id} for the metrics pass."""
    ids: dict[str, str] = {}
    for row in rows:
        result = await session.execute(
            text("""
                INSERT INTO modules (id, name, module_type, functions, description)
                VALUES (
                    gen_random_uuid(),
                    :name,
                    CAST(:module_type AS moduletype),
                    CAST(:functions AS modulefunction[]),
                    :description
                )
                ON CONFLICT (name) DO UPDATE
                    SET module_type = EXCLUDED.module_type,
                        functions   = EXCLUDED.functions,
                        description = EXCLUDED.description
                RETURNING id
                """),
            {
                "name": row["name"],
                "module_type": row["module_type"],
                # A real Python list, NOT a '{A,B}' array literal. asyncpg encodes arrays itself via
                # prepared-statement binds and rejects the string form that psycopg2 would accept
                # ("a sized iterable container expected"). The CAST in the SQL supplies the element
                # type; the driver supplies the elements.
                "functions": row.get("functions", []),
                "description": row.get("description"),
            },
        )
        ids[row["name"]] = str(result.scalar_one())
    return ids


async def _upsert_metrics(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    module_ids: dict[str, str],
    concept_ids: dict[str, str],
) -> int:
    """Upsert metrics, resolving module/concept names to ids from the earlier passes."""
    for row in rows:
        module_name = row["module"]
        if module_name not in module_ids:
            raise KeyError(f"metric {module_name}.{row['column_key']} names an unlisted module")
        concept_name = row.get("concept")
        if concept_name is not None and concept_name not in concept_ids:
            raise KeyError(f"metric {module_name}.{row['column_key']} names an unlisted concept '{concept_name}'")

        await session.execute(
            text("""
                INSERT INTO metrics (
                    id, module_id, concept_id, column_key, display_name,
                    value_type, unit, direction, property_categories,
                    variant_kind, variant, provenance, notes
                )
                VALUES (
                    gen_random_uuid(), CAST(:module_id AS uuid), CAST(:concept_id AS uuid),
                    :column_key, :display_name,
                    CAST(:value_type AS metricvaluetype), :unit,
                    CAST(:direction AS direction), CAST(:property_categories AS varchar[]),
                    CAST(:variant_kind AS variantkind), :variant,
                    CAST(:provenance AS provenance), :notes
                )
                ON CONFLICT ON CONSTRAINT uq_metric_identity DO UPDATE
                    SET concept_id          = EXCLUDED.concept_id,
                        display_name        = EXCLUDED.display_name,
                        value_type          = EXCLUDED.value_type,
                        unit                = EXCLUDED.unit,
                        direction           = EXCLUDED.direction,
                        property_categories = EXCLUDED.property_categories,
                        provenance          = EXCLUDED.provenance,
                        notes               = EXCLUDED.notes
                """),
            {
                "module_id": module_ids[module_name],
                "concept_id": concept_ids[concept_name] if concept_name else None,
                "column_key": row["column_key"],
                "display_name": row["display_name"],
                "value_type": row["value_type"],
                "unit": row.get("unit"),
                "direction": row.get("direction", "NEUTRAL"),
                "property_categories": row.get("property_categories", []),  # list, not '{...}' — see modules above
                "variant_kind": row.get("variant_kind"),
                "variant": row.get("variant"),
                "provenance": row.get("provenance", "INFERRED"),
                # The caveat that has to travel with the number (added in migration 006). NULL for
                # the uncurated metrics — an absent note is honest, an invented one is not.
                "notes": row.get("notes"),
            },
        )
    return len(rows)


async def seed(dry_run: bool = False) -> None:
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    concepts = catalog["concepts"]
    modules = catalog["modules"]
    metrics = catalog["metrics"]

    if dry_run:
        print(f"{_CATALOG.relative_to(_REPO_ROOT)} parses: ")
        print(f"  {len(concepts)} concepts, {len(modules)} modules, {len(metrics)} metrics")
        named_modules = {m["name"] for m in modules}
        named_concepts = {c["name"] for c in concepts}
        # Same referential checks the real run makes, without touching the database.
        for m in metrics:
            if m["module"] not in named_modules:
                print(f"  ERROR: metric {m['module']}.{m['column_key']} names an unlisted module")
            if (c := m.get("concept")) and c not in named_concepts:
                print(f"  ERROR: metric {m['module']}.{m['column_key']} names an unlisted concept '{c}'")
        return

    # One transaction for the whole seed: a half-applied catalog (modules without their metrics)
    # would be worse than no catalog, and the whole thing is small enough to commit atomically.
    async with AsyncSessionLocal() as session:
        concept_ids = await _upsert_concepts(session, concepts)
        module_ids = await _upsert_modules(session, modules)
        n_metrics = await _upsert_metrics(session, metrics, module_ids, concept_ids)
        await session.commit()

    print(f"seeded {len(concept_ids)} concepts, {len(module_ids)} modules, {n_metrics} metrics")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the metric catalog from seed/catalog.json")
    parser.add_argument("--dry-run", action="store_true", help="parse and validate without writing")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
