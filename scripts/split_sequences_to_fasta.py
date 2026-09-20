#!/usr/bin/env python3
"""Extract per-chain FASTA seed files from a Bio Discovery results CSV.

WHY THIS EXISTS
---------------
A Bio Discovery export's ``sequence`` column is NOT one sequence — it's the
candidate's chains joined by ``/`` (see docs/first-run-findings.md §3). For the
round-1 HER2 nanobody run that was ``nanobody / target`` — e.g.::

    QVQLVESGGG…VTVS / TQVCTG…CPIN
    ^ 127-aa VHH heavy chain   ^ 607-aa HER2 target (may carry XXXX unresolved runs)

Chaining one run into the next (e.g. feeding round-1 designs into EvoProtGrad as
round-2 *seed sequences*) needs those chains split back out into a FASTA whose
headers are the single-char chain labels Bio Discovery requires: ``H`` (heavy),
``L`` (light), ``T`` (target). This is the same H/L/T convention the
``candidate_chains`` table models — this script is the import-time inverse of that
slash-joined blob.

For directed-evolution seeds the target is dropped by default (``--antibody-only``
is implied by the default ``--chains H``), because "Target chains are not evolved"
and the docking module takes the target structure separately.

USAGE
-----
    # default: emit the heavy chain only, one FASTA per candidate row
    python scripts/split_sequences_to_fasta.py experiment_results/experiment_1/results.csv \\
        --out-dir experiment_results/round2_seeds

    # a full H+L antibody plus the target, positions matched to the slash order
    python scripts/split_sequences_to_fasta.py results.csv --chains H,L,T --out-dir out/

The ``--chains`` list is POSITIONAL: it maps to the ``/``-split parts in order, so
it must match how that particular export lays its chains out. Chains you name are
kept and labeled; parts beyond the list are ignored. Naming fewer chains than the
row has is how you drop the target (default ``H`` keeps only the first part).
"""

from __future__ import annotations

import argparse
import csv
import sys
import textwrap
from pathlib import Path

FASTA_WRAP = 60  # standard FASTA line width


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv_path", type=Path, help="Bio Discovery results CSV")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="directory to write FASTA files into (created if missing)",
    )
    p.add_argument(
        "--chains",
        default="H",
        help="comma-separated chain labels, positionally matched to the slash-split "
        "sequence parts (default: 'H' — heavy chain only, target dropped)",
    )
    p.add_argument(
        "--sequence-column",
        default="sequence",
        help="name of the column holding the slash-joined sequence (default: 'sequence')",
    )
    p.add_argument(
        "--id-column",
        default="id",
        help="name of the column holding the per-candidate id, used in filenames (default: 'id')",
    )
    return p.parse_args(argv)


def to_fasta(labels: list[str], parts: list[str]) -> str:
    """One FASTA record per (label, part) pair, sequences wrapped to FASTA_WRAP."""
    blocks: list[str] = []
    for label, seq in zip(labels, parts, strict=False):
        wrapped = "\n".join(textwrap.wrap(seq.strip(), FASTA_WRAP))
        blocks.append(f">{label}\n{wrapped}")
    return "\n".join(blocks) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    labels = [c.strip() for c in args.chains.split(",") if c.strip()]

    if not args.csv_path.exists():
        print(f"error: {args.csv_path} not found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    with args.csv_path.open() as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("error: CSV has no data rows", file=sys.stderr)
        return 1
    if args.sequence_column not in rows[0]:
        print(f"error: no '{args.sequence_column}' column in {args.csv_path}", file=sys.stderr)
        return 1

    written = 0
    for i, row in enumerate(rows, 1):
        parts = row[args.sequence_column].split("/")
        if len(parts) < len(labels):
            print(
                f"warning: row {i} has {len(parts)} chain part(s) but {len(labels)} label(s) "
                f"requested — writing what matches",
                file=sys.stderr,
            )
        cid = str(row.get(args.id_column, i))[:8]
        out_path = args.out_dir / f"seed_cand{i}_{cid}.fasta"
        out_path.write_text(to_fasta(labels, parts))
        kept = ", ".join(f"{lbl}={len(p.strip())}aa" for lbl, p in zip(labels, parts, strict=False))
        print(f"wrote {out_path}  ({kept})")
        written += 1

    print(f"\n{written} file(s) written to {args.out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
