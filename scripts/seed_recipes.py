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
from typing import Any, Final, cast

from sqlalchemy import CursorResult, text
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
    # `str`, not `ModuleName`, on purpose. This is where a raw name ENTERS the system from a
    # CSV header or a seed file, which is the boundary the aliases exist to have — see stage 5
    # of docs/type-safety-plan.md. `MetricKey` mirrors an app-side type and does carry them;
    # local seeder plumbing does not.
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


async def _resolve_modules(session: AsyncSession, names: list[str]) -> list[str]:
    """Map module names to ids, warning about any the catalog does not know.

    Resolving up front means the SAME set drives both identity lookup and linking. If a name had no
    row we would otherwise store a subset of what we searched by, and the recipe would never match
    itself on the next run.
    """
    resolved: list[str] = []
    for name in names:
        row = (await session.execute(text("SELECT id FROM modules WHERE name = :n"), {"n": name})).first()
        if row is None:
            # The module catalog is behind the corpus — run seed_metric_skeleton.py, which
            # auto-registers every module that emitted a column.
            print(f"  WARNING: no module row named '{name}' — excluded from this recipe")
            continue
        resolved.append(str(row[0]))
    return sorted(resolved)


async def _get_or_create_recipe(session: AsyncSession, name: str, module_ids: list[str]) -> str:
    """Find the recipe whose module set is EXACTLY this one, else create it.

    Identity is the module set, so that is what the lookup matches on. Matching on the derived
    `name` instead would be wrong: `recipe_name` is built from the headline modules plus a count, so
    two genuinely different pipelines that share their headline modules and module count collapse to
    the same string — for example {rfantibody, ..., hdbscan} and {rfantibody, ..., mmseqs}, both 11
    modules, both "RFantibody + ESM2 (11 modules)". The second would then reuse the first's row and
    its own modules would be merged in, silently fusing two pipelines into one recipe.

    HAVING array_agg(...) = the sorted id array is an exact set comparison: same members, same
    count. A recipe that merely CONTAINS these modules does not match.
    """
    existing = await session.execute(
        text("""
            SELECT r.id
              FROM recipes r
              JOIN recipe_modules rm ON rm.recipe_id = r.id
             WHERE r.recipe_type = :rtype
             GROUP BY r.id
            HAVING array_agg(rm.module_id::text ORDER BY rm.module_id::text) = CAST(:module_ids AS text[])
            """),
        {"rtype": RECIPE_TYPE, "module_ids": module_ids},
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


async def _link_modules(session: AsyncSession, recipe_id: str, module_ids: list[str]) -> None:
    """Populate recipe_modules from ids already resolved by _resolve_modules.

    Membership only — the edges have nowhere to go (see the TODO at the top of this module).
    """
    for module_id in module_ids:
        await session.execute(
            text("""
                INSERT INTO recipe_modules (recipe_id, module_id)
                VALUES (CAST(:recipe_id AS uuid), CAST(:module_id AS uuid))
                ON CONFLICT (recipe_id, module_id) DO NOTHING
                """),
            {"recipe_id": recipe_id, "module_id": module_id},
        )


async def seed(dry_run: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        experiments = await _experiment_module_sets(session)

        # Group by the module set — this is what makes one reusable recipe serve several runs.
        groups: dict[tuple[str, ...], list[tuple[str, str]]] = {}
        for exp_id, exp_name, modules in experiments:
            groups.setdefault(tuple(modules), []).append((exp_id, exp_name))

        if dry_run:
            print(f"{len(experiments)} experiments -> {len(groups)} recipes\n")
            for module_set, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                print(f"  {recipe_name(list(module_set))}  <- {len(members)} experiment(s)")
                for _, name in members:
                    print(f"      {name}")
            return

        linked = 0
        for module_set, members in groups.items():
            # Resolve first: the same id set is then used for BOTH the identity lookup and linking.
            module_ids = await _resolve_modules(session, list(module_set))
            recipe_id = await _get_or_create_recipe(session, recipe_name(list(module_set)), module_ids)
            await _link_modules(session, recipe_id, module_ids)
            for exp_id, _ in members:
                # COALESCE so a hand-corrected recipe_id is never overwritten by a re-run.
                result = await session.execute(
                    text("""
                        UPDATE experiments SET recipe_id = COALESCE(recipe_id, CAST(:recipe_id AS uuid))
                         WHERE id = CAST(:exp_id AS uuid) AND recipe_id IS NULL
                        """),
                    {"recipe_id": recipe_id, "exp_id": exp_id},
                )
                # `rowcount` is a CursorResult attribute; `session.execute()` is typed as Result.
                linked += cast("CursorResult[Any]", result).rowcount or 0
        await session.commit()

    print(f"{len(groups)} recipes derived from {len(experiments)} experiments; {linked} newly linked")


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive recipes from emitted score columns")
    parser.add_argument("--dry-run", action="store_true", help="report the grouping without writing")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
