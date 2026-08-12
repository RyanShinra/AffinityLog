#!/usr/bin/env python3
"""Load THIS corpus — the 2026-07 HER2 run — into an empty database.

WHY THIS LIVES HERE AND NOT IN ``scripts/``
-------------------------------------------
``scripts/load_experiment.py`` is general: point it at any Bio Discovery export and it loads.
Anyone importing from AWS wants that, so it stays put.

This file is the opposite — it is *data about one corpus* that happens to be executable. It
hard-codes seven specific filenames and the names their console runs had. Sitting next to
``CORPUS_MANIFEST.md``, whose Console-name column it transcribes, it reads as what it is: the
provenance record, kept runnable so a cold machine can rebuild the database without re-deriving
which files to load.

WHICH FILES, AND WHY NOT ALL ELEVEN (measured 2026-08-12)
---------------------------------------------------------
Eleven CSVs are in this tree; **seven** are loaded, and they produce nine experiment rows because
``load_csv`` creates one Experiment per distinct ``experimentId`` in a file — experiments 1 and 5
each span two sub-experiments and split. The result is exactly the corpus CLAUDE.md describes:

    9 experiments, 14 candidates, 26 chains, 200 distinct score keys

The four that are NOT loaded:

  * ``experiment_1/results (1).csv`` and ``(2).csv`` — the two sub-experiment exports of
    experiment 1. ``results.csv`` is the combined export of the same two runs, so loading all
    three would double-count experiment 1.

  * ``Experiment_9/…`` and ``experiment_10/…`` — the ESM2 pair. Both exported successfully, so
    the manifest counts them among the nine good runs. They are held back because **they have no
    predicted structures**: the ESM2 Only recipe is a property predictor and folds nothing, so
    there are no ``<candidateID>_boltz2.pdb`` files to pair with their candidates. The manifest
    says the same thing from the other side — "Boltz2 folds exist for exp 1/2/3/5/6/8/11",
    which is exactly the seven loaded above.

    This is a "not yet", not a "never" — they will almost certainly be loaded once we have a
    firmer handle on what their data means. Two things to expect when that happens: the key
    count goes 200 → 204 (the extra four being
    ``esm2propertypredictor.{name,predicted_property}.{H,L}``), so any doc quoting 200 needs
    revisiting; and that module's ``predicted_property`` column is generic, so which of the five
    properties a row reports is only recoverable via ``file id mapping.xlsx`` — see
    docs/schema-stress-log.md. Restoring them is a two-line uncomment below.

RE-RUNNING
----------
Idempotent by ``source_filename``: any CSV whose basename already has experiment rows is skipped.
That is deliberately a *filename* check, not a content hash — ``results (3).csv`` is not a
distinctive name, and this would not notice a file edited in place or a different file renamed to
match. Fine for a fixed corpus that will not be re-run; revisit if new exports ever arrive.

All seven load in ONE transaction and commit once, so a failure partway leaves the database
untouched rather than half-populated.

    python experiment_results/load_experiment_results.py            # load
    python experiment_results/load_experiment_results.py --dry-run  # show the plan only

Needs ``docker compose up`` and ``alembic upgrade head`` first. Seeding the catalog
(``seed_catalog`` → ``seed_metric_skeleton`` → ``seed_corpus_context`` → ``seed_recipes``) is a
separate step and is not run from here.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.importer.csv_importer import load_csv
from app.models.orm import Experiment

# Paths are relative to THIS file, not the working directory, so the script runs from anywhere.
_HERE = Path(__file__).parent

# (csv relative to _HERE, console name from CORPUS_MANIFEST.md).
# Experiments 1 and 5 carry an `experimentId` column and split into two Experiment rows each;
# `load_csv` appends " [<id prefix>]" to the name for those, so the names below stay unsuffixed.
CORPUS: list[tuple[str, str]] = [
    ("experiment_1/results.csv", "Testing De Novo Design"),
    ("experiment_2/results (3).csv", "HER 2 Round 9"),
    ("experiment_3/results (4).csv", "Experiment 3"),
    ("experiment_5/results 5 - Heavy and light.csv", "Experiment 5 - Light and Heavy"),
    ("experiment_6/results 6 - heavy light and target.csv", "Experiment 6 - Match H&L to Target"),
    ("experiment_8/results 8 - Boltz2 HLT.csv", "Her2 Round 8"),
    ("experiment_11/results 11 - Boltz2 evolved HLT dom4 fix.csv", "Her2 Boltz2 Evolved"),
    # The ESM2 pair — excluded on purpose; see the module docstring before re-enabling.
    # ("Experiment_9/results 9 - Evolved HL ESM2 Prediction full.csv", "ESM2 Prediction"),
    # ("experiment_10/results 10 - evolved HL Humanized ESM2 Prediction full.csv", "ESM2 Evolved"),
]


async def run(*, dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        # One round trip for the whole skip set, rather than an EXISTS per file.
        loaded_already: set[str] = {
            filename for filename in (await session.scalars(select(Experiment.source_filename).distinct())).all() if filename
        }

        experiments_created = 0
        candidates_created = 0
        chains_created = 0
        skipped = 0

        for relative_path, name in CORPUS:
            csv_path = _HERE / relative_path
            if not csv_path.exists():
                raise FileNotFoundError(f"{csv_path} is listed in CORPUS but not on disk")

            if csv_path.name in loaded_already:
                print(f"  skip  {relative_path}  (already loaded)")
                skipped += 1
                continue

            if dry_run:
                print(f"  load  {relative_path}  as {name!r}")
                continue

            experiments = await load_csv(session, csv_path, name=name)
            for experiment in experiments:
                chains = sum(len(candidate.chains) for candidate in experiment.candidates)
                experiments_created += 1
                candidates_created += len(experiment.candidates)
                chains_created += chains
                print(f"  load  {experiment.name}: {len(experiment.candidates)} candidate(s), {chains} chain(s)")

        if dry_run:
            print("\nDry run — nothing written.")
            return

        # One commit for all seven: the caller owns the transaction (see load_csv's docstring),
        # so nothing above has hit the database durably until this line.
        await session.commit()

        print(
            f"\nCommitted {experiments_created} experiment(s), "
            f"{candidates_created} candidate(s), {chains_created} chain(s); {skipped} file(s) skipped."
        )
        if skipped == 0:
            print("Expected for a full load of this corpus: 9 experiments, 14 candidates, 26 chains.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list what would load, write nothing")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
