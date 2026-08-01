#!/usr/bin/env python3
"""Derive recipes from the corpus and link each experiment to the one that produced it. Idempotent.

In Bio Discovery a recipe is a REUSABLE pipeline: you build it once, then configure it per run with
target molecules and parameters. So experiments relate to recipes many-to-one, and two runs of the
same pipeline must share a recipe row rather than each getting their own.

HOW A RECIPE IS IDENTIFIED HERE
-------------------------------
By the SET OF MODULES that emitted columns for its candidates — `split_part(key, '.', 1)` over the
`scores` JSONB bag. Experiments with identical module sets are treated as the same recipe.

That grouping is not merely plausible, it reproduces the campaign: 9 experiments collapse to 4
recipes, and the two Round 1 runs — the esm and amplify arms of one parameter sweep — land on a
single recipe, which is exactly what they were.

    !!! TODO — THIS IS A PROXY, NOT THE REAL RECIPE DEFINITION !!!

    Two things are missing and both are already recoverable from the archived HTML:

    1. THE EDGES. `recipe_modules` is a SET: it records that a recipe used EvoProtGrad and Boltz2,
       but not that EvoProtGrad FED Boltz2. `scripts/scrape_input_parameters.py::scrape_recipe_dag`
       already extracts real nodes and edges from the React Flow canvas
       (evoprotgrad -> boltz2, biophi -> humatchclassify, ...). Storing them needs an edge table or
       a JSONB topology column — a schema decision deliberately deferred; see
       docs/recipe-topology-note.md. The wiring is what explains the design->humanized row
       partition, so this is a real loss, not a cosmetic one.

    2. THE REAL NAMES. Bio Discovery names its recipes, and `scrape()` already returns that name
       from a saved Overview page. The names below are DERIVED from module composition instead, so
       they are descriptive rather than authentic.

    Until both land, every row this script writes carries recipe_type='derived-from-emitted-columns'
    so the database itself admits the provenance. Do not present these as the operator's recipes.

A module that ran but emitted no columns is invisible to this method — another reason the scraped
definition supersedes it.

    python scripts/seed_recipes.py            # derive and link
    python scripts/seed_recipes.py --dry-run  # report the grouping without writing
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

# Stamped on every derived recipe so the provenance is queryable, not just documented.
RECIPE_TYPE: Final[str] = "derived-from-emitted-columns"

# Modules that characterise a pipeline, most distinctive first. Used only to build a readable name;
# the recipe's IDENTITY is always the full module set. A fixed list (rather than "whatever is not
# common to all recipes") keeps names stable when new experiments are loaded.
_HEADLINE_MODULES: Final[list[tuple[str, str]]] = [
    ("rfantibody", "RFantibody"),
    ("evoprotgrad", "EvoProtGrad"),
    ("biophi", "BioPhi"),
    ("humatchclassify", "Humatch"),
    ("nanobodypolyreactivityscorer", "Polyreactivity"),
    ("esm2pseudo_log_likelihood", "ESM2"),
]


def recipe_name(modules: list[str]) -> str:
    """A readable, deterministic label derived from which headline modules are present."""
    present = [label for key, label in _HEADLINE_MODULES if key in modules]
    if not present:
        # No design or humanisation module at all — a fold-and-score-only pipeline.
        return f"Boltz2 fold only ({len(modules)} modules)"
    return f"{' + '.join(present)} ({len(modules)} modules)"


async def _experiment_module_sets(session: AsyncSession) -> list[tuple[str, str, list[str]]]:
    """(experiment_id, experiment_name, sorted module list) for every experiment with candidates."""
    result = await session.execute(text("""
            SELECT e.id::text, e.name,
                   array_agg(DISTINCT split_part(k, '.', 1) ORDER BY split_part(k, '.', 1))
              FROM experiments e
              JOIN candidates c ON c.experiment_id = e.id,
                   jsonb_object_keys(c.scores) k
             -- Only dotted keys are module output. Two keys ("tier", "recommendation") carry no
             -- module prefix at all: they are run-level verdicts the exporter attaches to the whole
             -- result, and `recommendation` is a paragraph of AI-generated prose. Without this
             -- filter split_part() returns them as if they were modules, which both inflates the
             -- module count in the derived name and then fails to link (no such module row),
             -- silently. scripts/extract_score_keys.py files them under a synthetic `_export`
             -- module for the same reason.
             WHERE position('.' in k) > 0
             GROUP BY e.id, e.name
             ORDER BY e.name
            """))
    return [(row[0], row[1], list(row[2])) for row in result.all()]


async def _get_or_create_recipe(session: AsyncSession, name: str) -> str:
    """Look-then-insert: `recipes` has no unique constraint on name (same gap as targets/projects)."""
    existing = await session.execute(
        text("SELECT id FROM recipes WHERE name = :name AND recipe_type = :rtype"),
        {"name": name, "rtype": RECIPE_TYPE},
    )
    if (row := existing.first()) is not None:
        return str(row[0])
    created = await session.execute(
        text("""
            INSERT INTO recipes (id, name, recipe_type) VALUES (gen_random_uuid(), :name, :rtype)
            RETURNING id
            """),
        {"name": name, "rtype": RECIPE_TYPE},
    )
    return str(created.scalar_one())


async def _link_modules(session: AsyncSession, recipe_id: str, modules: list[str]) -> None:
    """Populate recipe_modules. Membership only — the edges have nowhere to go (see the TODO)."""
    for module_name in modules:
        result = await session.execute(
            text("""
                INSERT INTO recipe_modules (recipe_id, module_id)
                SELECT CAST(:recipe_id AS uuid), m.id FROM modules m WHERE m.name = :module_name
                ON CONFLICT (recipe_id, module_id) DO NOTHING
                RETURNING module_id
                """),
            {"recipe_id": recipe_id, "module_name": module_name},
        )
        if result.first() is None:
            # Either already linked (a re-run) or there is no module row of that name. The latter
            # would mean the module catalog is behind the corpus — run seed_metric_skeleton.py,
            # which auto-registers every module that emitted a column. Check rather than assume,
            # because the failure is otherwise invisible: the INSERT ... SELECT simply matches no
            # rows and reports success.
            known = await session.execute(text("SELECT 1 FROM modules WHERE name = :n"), {"n": module_name})
            if known.first() is None:
                print(f"  WARNING: no module row named '{module_name}' — recipe link skipped")


async def seed(dry_run: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        experiments = await _experiment_module_sets(session)

        # Group by the module set — this is what makes one reusable recipe serve several runs.
        groups: dict[tuple[str, ...], list[tuple[str, str]]] = {}
        for exp_id, exp_name, modules in experiments:
            groups.setdefault(tuple(modules), []).append((exp_id, exp_name))

        if dry_run:
            print(f"{len(experiments)} experiments -> {len(groups)} recipes\n")
            for modules, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                print(f"  {recipe_name(list(modules))}  <- {len(members)} experiment(s)")
                for _, name in members:
                    print(f"      {name}")
            return

        linked = 0
        for modules, members in groups.items():
            recipe_id = await _get_or_create_recipe(session, recipe_name(list(modules)))
            await _link_modules(session, recipe_id, list(modules))
            for exp_id, _ in members:
                # COALESCE so a hand-corrected recipe_id is never overwritten by a re-run.
                result = await session.execute(
                    text("""
                        UPDATE experiments SET recipe_id = COALESCE(recipe_id, CAST(:recipe_id AS uuid))
                         WHERE id = CAST(:exp_id AS uuid) AND recipe_id IS NULL
                        """),
                    {"recipe_id": recipe_id, "exp_id": exp_id},
                )
                linked += result.rowcount or 0
        await session.commit()

    print(f"{len(groups)} recipes derived from {len(experiments)} experiments; {linked} newly linked")


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive recipes from emitted score columns")
    parser.add_argument("--dry-run", action="store_true", help="report the grouping without writing")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
