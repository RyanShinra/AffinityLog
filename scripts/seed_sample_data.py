#!/usr/bin/env python3
"""
Seed script: creates a sample experiment and imports the synthetic CSV fixture.
Run against a running local stack: python scripts/seed_sample_data.py

IMPORTANT: Uses synthetic placeholder data — not real experimental output.
"""

import asyncio
import sys
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000"
SAMPLE_CSV = Path(__file__).parent.parent / "sample_data" / "her2_nanobody_sample.csv"


async def seed() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        # Health check
        resp = await client.get("/health")
        resp.raise_for_status()
        print(f"Server status: {resp.json()['status']}")

        # Create experiment
        resp = await client.post(
            "/experiments",
            json={
                "name": "HER2 Nanobody — Synthetic Baseline",
                "recipe_name": "BoltzGen nanobody design",
                "target_name": "HER2 extracellular domain (PDB 1S78)",
                "target_pdb_id": "1S78",
                "notes": "SYNTHETIC DATA — placeholder until real Bio Discovery export is available.",
            },
        )
        resp.raise_for_status()
        experiment = resp.json()
        exp_id = experiment["id"]
        print(f"Created experiment: {exp_id} — {experiment['name']}")

        # Import candidates
        with SAMPLE_CSV.open("rb") as f:
            resp = await client.post(
                f"/experiments/{exp_id}/candidates/import",
                files={"file": ("her2_nanobody_sample.csv", f, "text/csv")},
            )
        resp.raise_for_status()
        result = resp.json()
        print(
            f"Import complete: {result['rows_imported']} imported, "
            f"{len(result['rows_skipped'])} skipped"
        )
        if result["column_mapping_warnings"]:
            print(f"Column warnings: {result['column_mapping_warnings']}")

        print(f"\nGraphQL playground: {BASE_URL}/graphql")
        print(f"Experiment ID for queries: {exp_id}")


if __name__ == "__main__":
    asyncio.run(seed())
