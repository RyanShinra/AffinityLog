# Stretch goal: ingest published antibody data

**Status:** goal, not started. Recorded 2026-07-31 so it doesn't stay folklore.

## The goal

Load **published antibody/antigen data from outside Amazon Bio Discovery** into AffinityLog —
even if the data is old, well-known, and not remotely novel. Novelty is not the point. The point is
that it is **data the schema did not grow up with**.

## Why it's worth doing

1. **It proves the schema is general, not bespoke.** Every row in the database today came out of one
   vendor's CSV export. A schema that only ever eats its author's own export format has not actually
   been tested — it has been fitted. Ingesting a source with different conventions, different column
   names, and a different idea of what a "candidate" is, is the real proof. If it needs a migration to
   absorb, the flexibility claim was overstated; if it only needs a new mapping, the claim holds.
2. **It supplies ground truth.** Everything in the corpus today is *predicted* — ipTM, pLDDT, OASis
   percentiles (see `demo-biology.md`). Published data can carry **measured** values: solved structures,
   experimentally determined epitopes, real affinities. That turns the demo from "here is what the model
   said" into "here is what the model said, and here is what was actually measured."
3. **Portfolio value.** A backend that reconciles two independent data sources is a substantially more
   interesting artifact than one that displays a single vendor's output.

## What already exists to build on

- **`candidates.scores` (JSONB) + `candidate_chains`** — the flexible pair. External scores land in the
  bag; external chains land as rows. No schema change should be required.
- **`Provenance` enum** on the relevant models — the hook for distinguishing "we predicted this" from
  "this was published," which becomes essential the moment both live in one table.
- **`antibody_hash`** (in `candidate_summary`) — the H+L fingerprint already used to trace one molecule
  across experiments. The same mechanism would match *our* trastuzumab-derived candidates to a
  *published* trastuzumab sequence, with no new machinery.
- **`BenchmarkDataset` / `BenchmarkResult`** — note these are a **different axis**: they model AWS's own
  DPBD validation statistics *per metric* (does this module's score correlate with a real developability
  assay?). Useful and related, but they are about scoring the *modules*, not about ingesting external
  *molecules*. Don't conflate the two; the goal here may need its own path.

## Candidate first target

**The trastuzumab–HER2 reference structure (`1N8Z`)** — already used in this project as the HER2
reference for the first real run (2026-07-04). It is the natural first ingest because:

- We already have predicted epitopes on HER2 for four candidates. A published, solved
  trastuzumab–HER2 structure gives a **measured epitope** to compare them against — directly testable,
  and it exercises the residue-numbering alignment we currently only sanity-check by eye.
- Our own corpus contains trastuzumab-derived molecules, so `antibody_hash` matching has something to
  match against.
- It is one entry, not a bulk import — a realistic scope for proving the path.

Other sources worth evaluating later (roughly increasing effort): therapeutic-antibody sequence
registries, structural antibody databases, and the repertoire data that BioPhi's OASis percentile is
already scored against.

## What "done" looks like

- At least one published molecule loaded as a candidate, distinguishable from Bio Discovery rows by
  provenance, with **no Alembic migration required** (a new mapping is fine; a schema change means the
  flexibility claim needs revising — and that finding should be written up either way).
- The demo can show a predicted result beside a measured one for the same target.
- A stress-log entry recording what the external source did that Bio Discovery never did.

## Before ingesting anything

**Check the licence and terms of each source first.** This project already treats terms-of-use as a gate
rather than an afterthought, and public availability is not the same as permission to redistribute.
Record what each source permits, in this file, before loading it.
