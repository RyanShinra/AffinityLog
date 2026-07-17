"""Load a Bio Discovery results CSV into Experiments + Candidates + CandidateChains.

WHY THIS SHAPE
--------------
Rebuilt from scratch against the 003 schema. The pre-redesign loader (commit ``6d40429``)
parsed a handful of columns into first-class ``humanness_score``/``binding_affinity_kd``
columns and swept the rest into a ``raw_scores`` bucket. Both are gone: ``candidates`` now
has exactly one ``scores`` JSONB bag (GIN-indexed), and *meaning* lives in the
``modules``/``metrics`` catalog, resolved at query time. So this loader is deliberately
dumb: it moves raw strings into the right rows and makes no interpretation.

Four real exports (docs/schema-stress-log.md) settled the design:

1. **Keying = the full CSV header** (option A). ``scores["esm2pseudo_log_likelihood.pseudo_perplexity.H"]``,
   not a bare ``pseudo_perplexity``. Bare keys are lossy — Run 1's ``temstapro.length.H`` and
   ``.T`` collapse onto each other. Bio Discovery enforces one instance of a module per
   recipe, so within a single experiment a header is unique *by construction*; headers only
   repeat across experiments, which ``experimentId`` disambiguates.

2. **Empty cell = "this module did not run on this sequence" (N/A), NOT zero and NOT a
   failed measurement.** Run 3's rows are one design + its BioPhi-humanized derivative;
   the humanness modules only scored the humanized row, so the original row's ``biophi.*``
   cells are blank. Empties are therefore *omitted* from the bag rather than stored.

3. **``sequenceType`` is the chain-label key.** It holds the labels for the ``/``-joined
   ``sequence`` column, positionally, per row (``'H/T'`` <-> 2 parts; ``'H'`` <-> 1 part).
   The export tells us the chain layout — no guessing, no pipeline knowledge needed.

4. **``experimentId`` may or may not exist.** The combined/parent export of a swept run
   carries it (one value per subexperiment); per-subexperiment and non-swept exports omit
   the column entirely. One CSV can therefore span N experiments — the importer must not
   assume ``1 CSV == 1 experiment``.

What this loader deliberately does NOT do: reconstruct candidate lineage. Run 3 proved the
humanized row is derived from the design row, but *nothing in the CSV links them* — no
parent id, no shared key. That link would have to be synthesized from pipeline knowledge,
and that decision is still open (see the schema-stress log).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import Candidate, CandidateChain, Chain, Experiment

# Columns that describe the row itself rather than a score a module emitted.
# Everything NOT in here is a module output and belongs in the scores bag.
STRUCTURAL_COLUMNS = frozenset({"id", "sequence", "sequenceType", "experimentId"})


def build_scores(row: dict[str, str]) -> dict[str, str]:
    """Turn one CSV row into the ``scores`` JSONB bag.

    This is the keystone: every downstream consumer (metrics-catalog resolution, the
    GraphQL ScoreEntry type) inherits whatever shape you choose here.

    The contract, per the design notes above:
      * key = the **full CSV header**, verbatim (option A) — e.g.
        ``"esm2pseudo_log_likelihood.pseudo_perplexity.H"``, ``"biophi.OASis Percentile_After.H"``.
        Headers may contain spaces; that's fine, JSONB keys are just strings.
      * value = the **raw string**, uncoerced (CLAUDE.md: "store raw strings, coerce
        downstream"). Real values include ``"<40"``, ``"-"``, semicolon lists and embedded
        JSON — floats are not a safe assumption.
      * skip ``STRUCTURAL_COLUMNS`` — they're identity/chain data, handled elsewhere.
      * skip **empty** cells entirely rather than storing ``""`` — empty means "this module
        didn't run on this sequence", which is not the same as a measured zero.

    >>> build_scores({"id": "abc", "sequence": "QVQ", "sequenceType": "H",
    ...               "boltz2.ptm": "0.93", "biophi.OASis Percentile_After.H": ""})
    {'boltz2.ptm': '0.93'}

    # YOUR TURN — ~5 lines. A dict comprehension over row.items() does it.
    """
    result: dict[str, str] = {}
    for key, value in row.items():
        if key not in STRUCTURAL_COLUMNS and len(value) > 0:
            result[key] = value
    return result


def parse_chains(row: dict[str, str]) -> list[CandidateChain]:
    """Split the ``/``-joined ``sequence`` into one CandidateChain per chain.

    ``sequenceType`` gives the labels positionally, so this is a straight zip. ``ordinal``
    disambiguates repeated labels (an export with two target chains would be ``'H/T/T'``),
    counted per-label so each ``T`` gets 0, 1, ...
    """
    labels = [lbl.strip() for lbl in row.get("sequenceType", "").split("/") if lbl.strip()]
    parts = [p.strip() for p in row.get("sequence", "").split("/") if p.strip()]

    if not labels:
        raise ValueError(f"row {row.get('id')!r}: no labels in sequenceType — refusing to guess")

    if len(labels) != len(parts):
        raise ValueError(
            f"row {row.get('id')!r}: sequenceType has {len(labels)} label(s) ({labels})"
            f"but sequence has {len(parts)} part(s) — refusing to guess"  # pyright: ignore[reportImplicitStringConcatenation]
        )

    seen: defaultdict[str, int] = defaultdict(int)
    chains: list[CandidateChain] = []
    for label, seq in zip(labels, parts, strict=True):
        chains.append(CandidateChain(chain=Chain(label), sequence=seq, ordinal=seen[label]))
        seen[label] += 1
    return chains


def group_rows_by_experiment(rows: list[dict[str, str]]) -> dict[str | None, list[dict[str, str]]]:
    """Group rows by ``experimentId``, or under a single ``None`` key if the column is absent.

    A combined/parent export of a swept run carries one ``experimentId`` per subexperiment;
    non-swept and per-subexperiment exports omit it. ``None`` therefore means "this whole
    CSV is one experiment", not "unknown experiment".
    """

    grouped: defaultdict[str | None, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (row.get("experimentId") or "").strip() or None
        grouped[key].append(row)
    return dict(grouped)


async def load_csv(
    session: AsyncSession,
    csv_path: Path,
    *,
    name: str,
    params: dict[str, object] | None = None,
) -> list[Experiment]:
    """Load ``csv_path`` into one Experiment per ``experimentId`` found (or exactly one).

    ``params`` is the operator-transcribed provenance — model, hotspots, framework, module
    config — which the export cannot carry (see first-run-findings §8). It is stamped onto
    every Experiment this call creates; when a CSV spans a sweep, per-subexperiment values
    (e.g. ``model=esm`` vs ``amplify``) must be reconciled by the caller afterwards.

    Returns the created Experiments (not yet committed — the caller owns the transaction).
    """
    with csv_path.open(newline="") as fh:
        # This clever library function will [apparently] read the header row and mate it to a dictionary for each row.
        # Each row is a dictionary mapping column names to values. The file, then, is a list of those.
        rows: list[dict[str, str]] = list(csv.DictReader(fh, restval=""))

    if not rows:
        raise ValueError(f"{csv_path} has no data rows")

    required_structural_cols: frozenset[str] = STRUCTURAL_COLUMNS - {
        "experimentId"
    }  # experimentId is legitimately optional

    missing_structural_cols: frozenset[str] = required_structural_cols - rows[0].keys()
    if missing_structural_cols:
        raise ValueError(
            f"{csv_path} missing required column(s): {sorted(missing_structural_cols)}"
        )

    experiments: list[Experiment] = []

    for experiment_id, group in group_rows_by_experiment(rows).items():
        experiment = Experiment(
            name=name if experiment_id is None else f"{name} [{experiment_id[:8]}]",
            source_filename=csv_path.name,
            params={**(params or {}), **({"experimentId": experiment_id} if experiment_id else {})},
        )
        for row in group:
            experiment.candidates.append(
                Candidate(
                    sequence_id=row["id"],
                    scores=build_scores(row),
                    chains=parse_chains(row),
                )
            )
        session.add(experiment)
        experiments.append(experiment)

    return experiments
