#!/usr/bin/env python3
"""Load one Bio Discovery results CSV into the database.

A thin runner around ``app.importer.csv_importer.load_csv``: open a session, load,
commit, report. This is the throwaway-CLI stand-in for the eventual REST import
endpoint (``POST /experiments/{id}/candidates/import``) — it exists so we can land
real data before the API layer is rebuilt.

    python scripts/load_experiment.py \\
        "experiment_results/experiment_5/results 5 - Heavy and light.csv" \\
        --name "HER2 Round 5 - trastuzumab H/L sweep"

Reads the DB URL from ``app.config.settings`` (defaults to the local Docker Postgres
on ``localhost:5432``), so make sure ``docker compose up`` is running and migrations
are at head first.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.database import AsyncSessionLocal
from app.importer.csv_importer import load_csv


async def run(csv_path: Path, name: str) -> None:
    async with AsyncSessionLocal() as session:
        experiments = await load_csv(session, csv_path, name=name)
        await session.commit()
        # expire_on_commit=False (see app/database.py) keeps these readable post-commit.
        print(f"Loaded {len(experiments)} experiment(s) from {csv_path.name}:")
        for experiment in experiments:
            chains = sum(len(c.chains) for c in experiment.candidates)
            print(
                f"  - {experiment.name}: "
                f"{len(experiment.candidates)} candidate(s), {chains} chain(s)"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv_path", type=Path, help="Bio Discovery results CSV to load")
    parser.add_argument("--name", required=True, help="human-readable experiment name")
    args = parser.parse_args()

    if not args.csv_path.exists():
        parser.error(f"{args.csv_path} not found")
    asyncio.run(run(args.csv_path, args.name))


if __name__ == "__main__":
    main()
