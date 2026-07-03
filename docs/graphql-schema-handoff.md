# Chapter 3 — GraphQL Schema-First Redesign (handoff)

> Read this cold on the Mac to continue. Authored on the PC at the end of a session that did
> ToS due diligence on the Module Evaluation scrape, scaffolded migration `003`, and drew the
> first full ERD. Companion docs: `docs/catalog-seed-plan.md` (Chapter 2), `docs/schema-erd.md`
> (current + planned schema, mermaid), `bio-discovery-scrape-handoff.md` (the scrape itself).

## Branch state — action needed first, before anything else

Three branches, but they're **not actually divergent** — `catalog-seed` and `metrics-scrape`
sit on the exact same linear commit history, `catalog-seed` is just two commits behind. There
is nothing to rebase. As of this handoff:

```
main (dcae469, origin/main)
 └─ catalog-seed (79440e2, origin/catalog-seed)
     └─ 9c5e640  Create bio-discovery-scrape-handoff.md
     └─ 663b672  Scaffold migration 003 TODOs in orm.py
     └─ e92314b  Add schema ERD doc
          = metrics-scrape (HEAD, origin/metrics-scrape)
```

(`d96781a`, the scrape data commit, sits between `9c5e640` and `663b672` in the real graph —
simplified above for readability. Run `git log --oneline --graph --all` for the exact order.)

**Decision deferred to Ryan, on the Mac:** fold `metrics-scrape` into `catalog-seed` only
(fast-forward, recommended — keeps `main` clean, matches the existing PR-reviewed pattern from
`dcae469`), or fast-forward `main` directly too. Either way it's a fast-forward, not a rebase —
no history rewrite, no force-push, regardless of which target is picked.

## What's done

- Migration `002` (`provenance` on `Metric`) — written, reviewed, not yet applied to any DB
  (alembic still stamped at `001` on the PC's local Postgres; Mac will need its own
  `alembic upgrade head` after building a fresh `.venv`).
- **ToS due diligence on the Module Evaluation scrape — resolved, cleared to proceed.** Full
  verbatim review of AWS Service Terms §1 + §22 (all 11 Bio Discovery subsections), the AUP,
  Customer Agreement §6.4, Responsible AI Policy, and the Legal hub's complete document list.
  No clause prohibits automated read-only access to your own authenticated account's displayed
  data; the two substantive Bio-Discovery-specific restrictions (22.5 no training a competing
  model, 22.6 no extracting model internals) don't apply to what was collected (published
  benchmark statistics, not experimental "Your Content" or model internals). Don't re-litigate
  this unless new facts surface.
- Raw scrape data in `seed/raw/module_evaluation_catalog.json` (**33** deduped metrics, updated
  2026-07-03 — was 27; see `bio-discovery-scrape-handoff.md` §6, a 6-entry merge bug where raw and
  `-transformed` cards collapsed onto the same key was found and fixed via manual reconciliation
  against the live Table view) and `module_evaluation_details.json` (190 metric×property rows,
  fully unique on `(module, param, column, module_output, property)`). Every catalog entry now also
  carries a `module_output` field (human-readable card title) — worth a place in the `Module`/
  `Metric` schema. **Still known incomplete beyond the fixed 6:** most other rows still have
  `"transform": null` even where the UI shows a populated Transformation panel (Mahalanobis μ/σ
  etc.) — the original automation didn't reliably capture that panel before running out of
  context. Not blocking (the `Metric.transform` JSONB column already exists, backfills fine later).
- `docs/schema-erd.md` — full current + planned ERD, mermaid, renders on GitHub natively and in
  VS Code with any mermaid extension.

## Queued, not yet typed: migration 003

Full field-level spec is already written as `YOUR TURN` comments directly in
`app/models/orm.py` (search for `migration 003` / `BenchmarkDataset` / `BenchmarkResult`) —
don't re-derive it, read those comments. Summary:

- `Module.version`, `Module.license` (both nullable strings)
- New table `benchmark_datasets` — the DPBD reference dataset description, plus the binarization
  methodology folded into `description` as prose (Titer > 0.1 mg/mL, median±1.5×IQR for others,
  undefined for Hydrophobicity — bimodal, not a scrape gap)
- New table `benchmark_results` — one row per (metric, property); `property` is a plain `String`
  not an enum (confirmed via live browsing that the vocabulary goes finer than the 9 filter
  pills — e.g. "Titer", "Analytical Size Exclusion Chromatography" aren't pills at all)
- `Metric.benchmark_results` relationship (other side of the new FK)

## Known design gap — decide via the GraphQL schema, not before it

**`Candidate.scores` (JSONB) has no relational path back to `Metric`.** Traced live in this
session: to resolve one score's meaning you'd need `Candidate → Experiment → Recipe →
(recipe_modules) → Module → Metric.column_key match`, and it's genuinely ambiguous when a
recipe's modules share a `column_key` with different variants — nothing records which module
produced a given candidate's value. Three options on the table, deliberately not decided yet:

1. Leave it as-is, resolve best-effort in application code, accept rare ambiguity.
2. Replace `scores` JSONB with a proper `candidate_scores` join table (`candidate_id`,
   `metric_id`, `value`), resolved once at **import time** by the CSV importer.
3. Hybrid: keep `scores` JSONB as the no-questions-asked ingestion store (this was the explicit
   design rationale in `CLAUDE.md`), add a thin best-effort `candidate_score_links` resolution
   table alongside it.

This is exactly why the GraphQL schema should come first: `app/graphql/types.py` and
`app/graphql/schema.py` are both currently just `# Intentionally cleared for the schema-first
redesign.` — no existing contract to protect. Design what a query needs to return for a
Candidate's scores (raw values only? resolved Metric metadata? both?) and that answers which of
the three options above is worth its cost.

**Update (post-PR#2 review, 2026-07-02):** Ryan's leaning toward option 2 (or the option-3
hybrid) — motivated by wanting to write meaningful queries directly ("show me candidates scored by
metrics with AUROC > X", "compare candidates across two recipe runs that share a metric"), not just
by the ambiguity problem. Direction: build an explicit relation from `Candidate` to the specific
result that scored it, resolved once at import time rather than re-derived per query. Still open
before this gets typed:
- Does "the result that scored it" mean a link to `Metric` (which metric produced this value), or
  something richer that also carries the `BenchmarkResult` context (§6 of
  `bio-discovery-scrape-handoff.md`) the value should be judged against? These are different
  tables for different purposes — `BenchmarkResult` describes a metric's *validation* against
  DPBD, not any individual candidate's score — so "relation to the result used to score them" most
  likely means `Candidate` → `Metric` (a `candidate_scores` join table per option 2), with
  `BenchmarkResult` reachable transitively via `Metric` for interpretation (e.g. "is this
  candidate's score good, per the AUROC this metric achieved on DPBD?"). Confirm before typing.
- Table shape: `candidate_scores(candidate_id, metric_id, value)` per option 2's sketch — worth
  deciding here whether `value` duplicates what's already in `Candidate.scores` JSONB or replaces
  it outright.

**Update (2026-07-03):** the scrape merge-bug fix (§6 of `bio-discovery-scrape-handoff.md`) split 6
raw/`-transformed` card pairs into separate catalog entries, which surfaced a related Metric-side
question worth deciding alongside the Candidate/scores one above, since both affect the same
`app/graphql/types.py` design pass: `Metric` gets a `VariantKind.TRANSFORM` member and a
self-referential `transform_of_metric_id` (§7 of `bio-discovery-scrape-handoff.md`, scaffolded as
YOUR-TURN comments in `orm.py`, not typed yet). Worth deciding now, before the GraphQL types are
written, not after: does a `Metric` type expose its raw/transformed sibling as a queryable field
(e.g. `metric.transformOf` / `metric.transformedVariant`), or stay opaque and let the importer
resolve it internally? Given the whole point of this project's GraphQL-first ordering is that "richer
queries for a future frontend" is the actual goal (not just fixing the ambiguity), leaning toward
exposing it — a frontend comparing a metric's raw vs. transformed behavior is a realistic query
shape given the SFvCSP AuROC-divergence finding in §6.

## Next steps, in order

1. Resolve the branch-fold decision above.
2. **New branch off the consolidated tip** for the GraphQL work — don't build it on
   `metrics-scrape`/`catalog-seed` directly, per Ryan's explicit call.
3. Design `app/graphql/types.py` / `schema.py` (Strawberry) for at least the catalog side
   (Module, Metric, Concept), the Candidate/scores question above, and the Metric
   transform-lineage question just above.
4. Only after that's settled, finish typing migration `003` and run
   `alembic revision --autogenerate -m "..."`.
