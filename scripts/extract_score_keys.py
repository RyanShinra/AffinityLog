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
import re
from collections import defaultdict
from typing import Final, NamedTuple

from sqlalchemy import text

from app.database import AsyncSessionLocal

# Trailing .H/.L/.T — the chain the value was measured on, not part of the metric's identity.
_CHAIN_SUFFIX: Final[re.Pattern[str]] = re.compile(r"\.(H|L|T)$")

# Columns whose name encodes a run PARAMETER rather than naming a distinct quantity. EvoProtGrad
# reports the same statistic once per protein language model and distinguishes them by prefixing
# the model name — so `esm_pseudolikelihood_ratio` and `amplify_pseudolikelihood_ratio` are ONE
# metric with two variants, not two metrics. Splitting them here is what lets a consumer ask for
# "pseudolikelihood ratio" and get both arms of the sweep.
#
# Format: module -> (regex with a `variant` and `column` group, VariantKind name)
_VARIANT_RULES: Final[dict[str, tuple[re.Pattern[str], str]]] = {
    "evoprotgrad": (
        re.compile(r"^(?P<variant>esm|amplify)_(?P<column>pseudolikelihood_ratio)$"),
        "PARAMETER",
    ),
}


class MetricKey(NamedTuple):
    """One row of the metric catalog's identity, as derived from observed data."""

    module: str
    column_key: str
    variant_kind: str | None
    variant: str | None
    chains: tuple[str, ...]  # which chain suffixes were seen for this metric (informational)
    raw_keys: tuple[str, ...]  # the literal JSONB keys that collapsed into this row


# Two keys in the corpus ("tier", "recommendation") carry NO module prefix — they are run-level
# verdicts the exporter attaches to the whole result, not a module's output. `recommendation` is
# even a paragraph of AI-generated prose. Filing them under a synthetic module keeps them in the
# skeleton (nothing silently lost) while flagging that they are not really module metrics.
_NO_MODULE: Final[str] = "_export"


def decompose(key: str) -> tuple[str, str, str | None, str | None, str | None]:
    """Split one raw JSONB key into (module, column_key, variant_kind, variant, chain)."""
    if "." not in key:
        return _NO_MODULE, key, None, None, None
    module, _, rest = key.partition(".")
    chain_match = _CHAIN_SUFFIX.search(rest)
    chain = chain_match.group(1) if chain_match else None
    if chain_match:
        rest = rest[: chain_match.start()]

    variant_kind: str | None = None
    variant: str | None = None
    rule = _VARIANT_RULES.get(module)
    if rule is not None:
        pattern, kind = rule
        if (m := pattern.match(rest)) is not None:
            variant_kind, variant, rest = kind, m.group("variant"), m.group("column")

    return module, rest, variant_kind, variant, chain


async def collect() -> list[MetricKey]:
    """Read every distinct score key from the corpus and collapse them into metric identities."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT DISTINCT k FROM candidates c, jsonb_object_keys(c.scores) k ORDER BY k"))
        raw_keys = [row[0] for row in result.all()]

    # identity -> (chains seen, raw keys that produced it)
    buckets: dict[tuple[str, str, str | None, str | None], tuple[set[str], list[str]]] = defaultdict(lambda: (set(), []))
    for key in raw_keys:
        module, column, variant_kind, variant, chain = decompose(key)
        chains, originals = buckets[(module, column, variant_kind, variant)]
        if chain:
            chains.add(chain)
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
                        "variant_kind": m.variant_kind,
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
            var = f"  ({m.variant_kind}={m.variant})" if m.variant else ""
            print(f"    {m.column_key}{var}{chains}")

    print(f"\n{'=' * 70}")
    print(f"{len(metrics)} metrics across {len(by_module)} modules, from {total_raw} raw keys")


if __name__ == "__main__":
    main()
