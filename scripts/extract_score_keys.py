#!/usr/bin/env python3
"""Derive the metric catalog's *skeleton* from the score keys actually present in the corpus.

WHY THIS EXISTS
---------------
`docs/catalog-seed-plan.md` was written before any real Bio Discovery data existed, so its plan was
to hand-curate the catalog from module READMEs — i.e. to guess what each module *might* emit. We now
have 9 real experiments, which inverts that: the corpus is ground truth for **what exists**, and the
READMEs are only needed for **what it means** (display name, unit, direction).

So this script answers "what are we seeding?" mechanically. It reads every key in every candidate's
`scores` JSONB bag and decomposes it into the catalog's identity fields. The output is the skeleton;
meaning is layered on afterwards, and `Metric.provenance` records which is which.

KEY ANATOMY
-----------
    module . column_key [. chain]

    temstapro.clash.H                          -> temstapro / clash            / chain H
    boltz2.protein_iptm                        -> boltz2    / protein_iptm     / no chain
    evoprotgrad.esm_pseudolikelihood_ratio.H   -> evoprotgrad / pseudolikelihood_ratio
                                                  + variant_kind=PARAMETER, variant="esm"

The chain suffix is deliberately NOT part of metric identity: `clash` is the same metric whether it
was measured on the heavy or the light chain. Chain says *which subject the value describes*, which
is why `VariantKind` has no CHAIN member. Stripping it is what collapses 200 keys to 138 metrics.

    python scripts/extract_score_keys.py           # grouped summary
    python scripts/extract_score_keys.py --json    # machine-readable, for seeding
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from typing import NamedTuple

from sqlalchemy import text

# `decompose` used to live in this file, which is where it was born. It moved to app/ because the
# GraphQL ScoreEntry resolver needs the SAME decomposition in the opposite direction — key in,
# catalog row out — and two copies would drift silently. See app/catalog/keys.py for the full
# reasoning and the key anatomy. This script is now one of its two callers, not its owner.
from app.catalog.keys import MetricIdentity, decompose
from app.catalog.variant_kind import VariantKind
from app.database import AsyncSessionLocal


class MetricKey(NamedTuple):
    """One row of the metric catalog's identity, as derived from observed data."""

    module: str
    column_key: str
    variant_kind: VariantKind | None
    variant: str | None
    chains: tuple[str, ...]  # which chain suffixes were seen for this metric (informational)
    raw_keys: tuple[str, ...]  # the literal JSONB keys that collapsed into this row


async def collect() -> list[MetricKey]:
    """Read every distinct score key from the corpus and collapse them into metric identities."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT DISTINCT k FROM candidates c, jsonb_object_keys(c.scores) k ORDER BY k"))
        raw_keys = [row[0] for row in result.all()]

    # identity -> (chains seen, raw keys that produced it)
    buckets: dict[MetricIdentity, tuple[set[str], list[str]]] = defaultdict(lambda: (set(), []))
    for key in raw_keys:
        module, column, variant_kind, variant, chain = decompose(key)
        chains, originals = buckets[(module, column, variant_kind, variant)]
        if chain:
            # `.value` because `chains` is informational output — printed, and serialised to JSON
            # by `--json`. The enum is the internal spelling; the letter is the reported one.
            chains.add(chain.value)
        originals.append(key)

    return [
        MetricKey(module, column, vk, v, tuple(sorted(chains)), tuple(originals))
        for (module, column, vk, v), (chains, originals) in sorted(buckets.items())
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a grouped summary")
    args = parser.parse_args()

    metrics = asyncio.run(collect())

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "module": m.module,
                        "column_key": m.column_key,
                        "variant_kind": m.variant_kind.name if m.variant_kind else None,
                        "variant": m.variant,
                        "chains": list(m.chains),
                        "raw_keys": list(m.raw_keys),
                    }
                    for m in metrics
                ],
                indent=2,
            )
        )
        return

    by_module: dict[str, list[MetricKey]] = defaultdict(list)
    for m in metrics:
        by_module[m.module].append(m)

    total_raw = sum(len(m.raw_keys) for m in metrics)
    for module in sorted(by_module, key=lambda mod: (-len(by_module[mod]), mod)):
        rows = by_module[module]
        print(f"\n{module}  ({len(rows)} metrics, {sum(len(r.raw_keys) for r in rows)} raw keys)")
        for m in rows:
            chains = f"  [{'/'.join(m.chains)}]" if m.chains else ""
            var = f"  ({m.variant_kind.name if m.variant_kind else None}={m.variant})" if m.variant else ""
            print(f"    {m.column_key}{var}{chains}")

    print(f"\n{'=' * 70}")
    print(f"{len(metrics)} metrics across {len(by_module)} modules, from {total_raw} raw keys")


if __name__ == "__main__":
    main()
