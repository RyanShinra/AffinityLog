#!/usr/bin/env python3
"""Populate this campaign's context tables: projects, targets, artifacts. Idempotent.

WHAT THIS IS (and how it differs from seed_catalog.py)
-----------------------------------------------------
Two kinds of reference data live in this schema and they should not be conflated:

  seed_catalog.py        modules / metrics / concepts  -- what a SCORE MEANS. Vendor reference
                                                          data, true regardless of what we ran.
  this script            projects / targets / artifacts -- what THIS CAMPAIGN is. Facts about our
                                                          own runs and the files they produced.

Everything here was already true; it just had nowhere to live. All 9 experiments were run against
HER2 with `target_id` and `project_id` NULL, and the 11 structure files existed only on disk, found
by globbing. That glob is why `app/routers/demo.py` needs a repo-root path and why structures 404
inside Docker without a bind mount — registering them as artifacts is what lets the demo ask the
database instead of the filesystem.

IDEMPOTENCE
-----------
Everything here is look-then-insert rather than `ON CONFLICT`, because none of these three tables
has a unique constraint to conflict on — see the note above `_get_or_create_target`. Adding
UNIQUE(name) to targets/projects and UNIQUE(uri) to artifacts would let this use real upserts and
would close the (harmless here, single-operator) race; it is worth a future migration.

Experiment links are only filled in where they are NULL, so a hand-corrected row is never
overwritten by a re-run.

    python scripts/seed_corpus_context.py            # load
    python scripts/seed_corpus_context.py --dry-run  # report what would change
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from typing import Any, Final, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_STRUCTURES_DIR: Final[Path] = _REPO_ROOT / "experiment_results"

# "<32-hex candidate id>_<tool>.pdb" — the tool suffix becomes Artifact.kind, so a candidate folded
# by two different tools gets two artifacts rather than one overwriting the other.
_PDB_NAME: Final[re.Pattern[str]] = re.compile(r"(?P<candidate>[0-9a-f]{32})_(?P<kind>[a-z0-9]+)\.pdb")

# The single target of every experiment in this corpus. 1N8Z is the trastuzumab-HER2 co-crystal
# used as the HER2 reference from the first real run onward (2026-07-04), and it is also the
# proposed first ingest for the published-data goal — see docs/published-data-goal.md.
TARGET_NAME: Final[str] = "HER2 extracellular domain (PDB 1N8Z)"
TARGET_PDB_ID: Final[str] = "1N8Z"

PROJECT_NAME: Final[str] = "HER2 binder campaign"
PROJECT_NOTES: Final[str] = (
    "Amazon Bio Discovery free-trial campaign, 2026-06 to 2026-07: 11 experiments against HER2 "
    "(9 exported successfully), covering de novo nanobody design, trastuzumab evolution, "
    "humanization, and standalone Boltz2 folds. Every experiment here shares one target."
)


# NOTE: unlike `modules` and `concepts`, the `targets` and `projects` tables have NO unique
# constraint on `name` — so `ON CONFLICT (name)` fails here with "no unique or exclusion constraint
# matching the ON CONFLICT specification". That asymmetry looks like an oversight in migration 001
# rather than a decision (these are the same sort of named reference entity), and a UNIQUE(name) on
# both would be worth a future migration.
#
# So these are get-or-create in two statements. The tidier single-statement version (INSERT ...
# WHERE NOT EXISTS, UNION ALL a lookup, inside a CTE) does not survive asyncpg: reusing :name in
# both an INSERT target-column position and a comparison makes it give up with "inconsistent types
# deduced for parameter $1". Look-then-insert has no type ambiguity, reads plainly, and the extra
# round-trip is irrelevant for a handful of seed rows.
#
# Strictly this is racy — two concurrent runs could both insert — which is exactly what the missing
# UNIQUE(name) would prevent. Acceptable for a single-operator seeder.
async def _get_or_create_target(session: AsyncSession) -> str:
    existing = await session.execute(text("SELECT id FROM targets WHERE name = :name"), {"name": TARGET_NAME})
    if (row := existing.first()) is not None:
        return str(row[0])
    created = await session.execute(
        text("INSERT INTO targets (id, name, pdb_id) VALUES (gen_random_uuid(), :name, :pdb_id) RETURNING id"),
        {"name": TARGET_NAME, "pdb_id": TARGET_PDB_ID},
    )
    return str(created.scalar_one())


async def _get_or_create_project(session: AsyncSession) -> str:
    existing = await session.execute(text("SELECT id FROM projects WHERE name = :name"), {"name": PROJECT_NAME})
    if (row := existing.first()) is not None:
        return str(row[0])
    created = await session.execute(
        text("INSERT INTO projects (id, name, notes) VALUES (gen_random_uuid(), :name, :notes) RETURNING id"),
        {"name": PROJECT_NAME, "notes": PROJECT_NOTES},
    )
    return str(created.scalar_one())


async def _link_experiments(session: AsyncSession, target_id: str, project_id: str) -> int:
    """Point every experiment at the campaign's target and project.

    A blanket assignment is correct *for this corpus* — all 9 runs are HER2 — but it is an
    assumption, not a law. `WHERE ... IS NULL` means a future non-HER2 experiment that was assigned
    a different target by hand keeps it.
    """
    result = await session.execute(
        text("""
            UPDATE experiments
               SET target_id  = COALESCE(target_id,  CAST(:target_id AS uuid)),
                   project_id = COALESCE(project_id, CAST(:project_id AS uuid))
             WHERE target_id IS NULL OR project_id IS NULL
            """),
        {"target_id": target_id, "project_id": project_id},
    )
    # `rowcount` is a CursorResult attribute; `session.execute()` is typed as Result.
    return cast("CursorResult[Any]", result).rowcount or 0


def discover_structures() -> list[tuple[str, str, str]]:
    """Find structure files on disk as (candidate_sequence_id, kind, repo-relative uri)."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(_STRUCTURES_DIR.glob("*/*.pdb")):
        match = _PDB_NAME.fullmatch(path.name)
        if match is None:
            continue  # not a candidate structure — leave it alone rather than guess
        uri = path.relative_to(_REPO_ROOT).as_posix()
        found.append((match.group("candidate"), match.group("kind"), uri))
    return found


async def _register_artifacts(session: AsyncSession, structures: list[tuple[str, str, str]]) -> tuple[int, int]:
    """Insert an artifact row per structure file. Returns (inserted, skipped_no_candidate)."""
    inserted = 0
    orphaned = 0
    for sequence_id, kind, uri in structures:
        # Look-then-insert, for the same asyncpg reason as the target/project helpers above: reusing
        # :uri in both a SELECT list and a NOT EXISTS comparison trips "inconsistent types deduced".
        already = await session.execute(text("SELECT 1 FROM artifacts WHERE uri = :uri"), {"uri": uri})
        if already.first() is not None:
            continue  # registered by an earlier run

        candidate = await session.execute(text("SELECT id FROM candidates WHERE sequence_id = :sid"), {"sid": sequence_id})
        if (row := candidate.first()) is None:
            # A structure file for a candidate that was never loaded. Report rather than skip
            # silently: it means the corpus on disk and the corpus in the database disagree.
            orphaned += 1
            print(f"  WARNING: {uri} names candidate {sequence_id[:8]}, which is not loaded")
            continue

        await session.execute(
            text("""
                INSERT INTO artifacts (id, candidate_id, kind, uri)
                VALUES (gen_random_uuid(), :candidate_id, :kind, :uri)
                """),
            {"candidate_id": row[0], "kind": kind, "uri": uri},
        )
        inserted += 1
    return inserted, orphaned


async def seed(dry_run: bool = False) -> None:
    structures = discover_structures()

    if dry_run:
        print(f"target:  {TARGET_NAME}")
        print(f"project: {PROJECT_NAME}")
        print(f"structures on disk: {len(structures)}")
        by_kind: dict[str, int] = {}
        for _, kind, _ in structures:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        for kind, n in sorted(by_kind.items()):
            print(f"  {kind}: {n}")
        return

    async with AsyncSessionLocal() as session:
        target_id = await _get_or_create_target(session)
        project_id = await _get_or_create_project(session)
        linked = await _link_experiments(session, target_id, project_id)
        inserted, orphaned = await _register_artifacts(session, structures)
        await session.commit()

    print(f"target + project upserted; {linked} experiments linked")
    print(f"artifacts: {inserted} registered, {len(structures) - inserted - orphaned} already present, {orphaned} orphaned")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed projects, targets and artifacts for this corpus")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
