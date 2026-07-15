# Chapter 3 — GraphQL Schema-First Redesign (handoff)

> Read this cold on the Mac to continue. Companion docs: `docs/catalog-seed-plan.md` (Chapter 2),
> `docs/schema-erd.md` (current + planned schema, mermaid — SQL ERD *and* the GraphQL apparent
> representation, side by side), `bio-discovery-scrape-handoff.md` (the scrape),
> `docs/first-run-findings.md` (the real Bio Discovery run + its schema implications).

## ⚠️ STATUS UPDATE (2026-07-15, Mac) — NEWEST; start here

**Migration `003` is MERGED TO MAIN** (PR #5, merge commit on `origin/main`) — including the
index cleanup + the repeatable-downgrade fix (the `chain` enum-type drop). The SQL schema side is
done and shipped. The 2026-07-13 note below is accurate but predates the merge and the index fix.

**Start the GraphQL work on a NEW branch off `main`** — don't build on `schema-003` (it's merged
and done). That was Ryan's explicit call: fresh branch off the consolidated tip.

**Real round-2 data is landing NOW and should inform the GraphQL types — don't design in a
vacuum.** As of this note, a round-2 Bio Discovery run is executing (EvoProtGrad directed evolution
seeded from round-1's lead nanobody → Boltz2 + sequence scorers), with a small sweep to follow
Thursday. See [[project_aws_trial_deadline]] for the run plan/status. Why it matters for `types.py`:
- **BioPhi is in the recipe → real humanness data.** This validates or reshapes the *speculative*
  `humanness_score` first-class column (nothing had confirmed Bio Discovery emits humanness before).
  Check the new export before committing the GraphQL shape for it.
- **EvoProtGrad emits directed-evolution columns** (sampling count, first-appearance iteration,
  PoE, pseudolikelihood ratio) — new `raw_scores` keys to make sure the JSONB/`ScoreEntry` design
  absorbs cleanly.
- **The sweep = multiple experiments over one HER2 target** — the real stress-test for
  `Experiment.params` and the "one CSV spans multiple experiments" handling (first-run-findings
  §7–8). GeoDock was dropped (wiring bug); Boltz2 covers the binding read.
- **Trial expires Fri 2026-07-17** — after that, no more runs; the data captured by then is all
  there'll be. Analyze the round-2 export(s) into `docs/first-run-findings.md` (or a round-2
  sibling) the way round 1 was.

**Supporting artifacts added this session** (committed alongside this note): `scripts/split_sequences_to_fasta.py`
(splits a results-CSV `sequence` cell into per-chain FASTA — the import-time inverse of
`candidate_chains`), `sample_data/structures/1N8Z_HER2_target_chainT.pdb` (the T-labeled target BD
actually uses; the older `1N8Z_chainC_HER2.pdb` is the raw C-labeled extract), and
`experiment_results/round2_seeds/` (the round-2 seed FASTAs).

**The actual next step is unchanged:** design + implement `app/graphql/types.py` and `schema.py`
(Strawberry) against the now-stable, now-merged schema. Read the GraphQL half of
`docs/schema-erd.md` first — it's the closest thing to a committed spec (the SDL was designed in
chat, mirrored there). Open questions already leaned (see below): `Candidate.scores` → resolver-time
`ScoreEntry` (JSONB, no backing table) won over a join table; transform lineage IS exposed
(`metric.transformOf`). PC catch-up if switching machines: `git pull` on `main`, rebuild `.venv`,
`docker compose up -d db`, `alembic upgrade head`.

## ⚠️ STATUS UPDATE (2026-07-13, Mac) — superseded by the 2026-07-15 note above; kept for detail

Migration `003` is now **written, applied, and verified drift-free** on branch `schema-003`.
The 2026-07-07 update below is still accurate for everything *except* its "next concrete step"
(that step is now done).

- **`003` is applied to the Mac's local DB.** `alembic upgrade head` cleared `002` (the fixed
  enum-casing bug) and `003` cleanly; `alembic_version` = `003`.
- **Verified against the live DB, not just the file:** all three new tables
  (`benchmark_datasets`, `benchmark_results`, `candidate_chains`) exist; `metrics` has
  `transform_of_metric_id` + `transform_stats` with a **named** self-FK
  (`fk_metrics_transform_of_metric_id`); `candidates.fasta_sequence` is dropped;
  `modules.version`/`license` added. A throwaway `--autogenerate` drift probe came back **empty
  (`pass`)** — ORM and DB are exactly in sync.
- **The `TRANSFORM` enum-casing trap (the 002 bug, round two) was caught before it shipped.**
  Autogenerate never emits enum-value additions, so `003` needed a hand-added
  `op.execute("ALTER TYPE variantkind ADD VALUE IF NOT EXISTS 'TRANSFORM'")`. Critically it's
  **uppercase `'TRANSFORM'`** (the member `.name`), not the lowercase `.value` the old orm.py
  comment suggested — SAEnum binds `.name`. Confirmed in `pg_enum`: label is `TRANSFORM`. The
  misleading comment is fixed, and a one-line invariant note now sits atop the enum section in
  `orm.py`.
- **Next concrete step: implement `app/graphql/types.py` / `schema.py`.** 003 has landed, so the
  SQL side is settled; the GraphQL SDL (designed in chat, mirrored in `docs/schema-erd.md`) can
  now be typed against a stable schema. PC catch-up: `git pull` on `schema-003`, then run
  `alembic upgrade head` against the PC's own local container (the migration file syncs via git;
  each machine applies it to its own DB separately).

## ⚠️ STATUS UPDATE (2026-07-07, PC) — read this before anything below

Most of this doc predates a lot of real progress and is now stale in places. Don't follow the
"Next steps" list at the bottom literally — read this section first.

- **PR #3 and PR #4 are both merged to `main`.** The branch-fold decision, the real Bio Discovery
  run, and the SQL≠GraphQL / ERD diagram work are all done and merged. See `git log main`.
- **The real run happened** (§ below is accurate) — findings are in `docs/first-run-findings.md`,
  not repeated here.
- **The GraphQL SDL was designed collaboratively in a chat session on the PC — not yet written
  into this doc.** Real gap: if picking this up fresh, the actual type shapes discussed
  (`Candidate`, `Experiment`, `ScoreEntry` with no backing table, `Module`/`Metric`/`Concept`,
  the annotate-only mutation surface) only exist in that conversation and in the GraphQL half of
  `docs/schema-erd.md`'s diagram + divergence table. Read that diagram first; it's the closest
  thing to a spec that's actually committed.
- **Decided, since the "known design gap" section below was written:** `Candidate.fasta_sequence`
  is gone — replaced by a `candidate_chains` child table (`Chain` enum H/L/T + `CandidateChain`
  model), because a run can have 0..N chains of each type, not exactly one antibody + one target.
  This resolves *part* of the old "how does Candidate relate to Metric" question by giving chains
  their own real relational model; `scores` (the JSONB score bag) is still the open piece — see
  `docs/schema-erd.md`'s `ScoreEntry` type (a GraphQL type backed by **no table**, resolved by
  parsing each JSONB key against the `metrics` catalog at query time). That's the current answer
  to the "candidate_scores join table vs. resolver-time JSONB interpretation" question below:
  **JSONB + resolver-time interpretation won**, not a join table.
- **`VariantKind.TRANSFORM` + `Metric.transform_of_metric_id`/`transform_of`/`transform_stats`
  are now fully typed** (not just scaffolded) — the "expose as `metric.transformOf`" question
  below is answered: yes, it's a real self-referential relationship now.
- **Migration `003`'s ORM models are complete** on branch `schema-003` (renamed from
  `graphql-schema` — it turned out to be SQL work, not GraphQL, so the name was wrong). Covers:
  `BenchmarkDataset`, `BenchmarkResult`, `Chain`/`CandidateChain`, `Module.version`/`license`,
  `VariantKind.TRANSFORM`, transform lineage. **Not yet migrated. Also: `002` itself had never
  actually been applied anywhere** (2026-07-07 finding) — a real bug (`PROVENANCE_COLUMN_DEFAULT`
  was lowercase `'inferred'`, but SQLAlchemy's `SAEnum(Provenance)` labels the Postgres enum by
  member `.name`, i.e. uppercase `INFERRED`/`AWS_CONFIRMED` — confirmed against the `direction`
  enum's real stored labels via `psql`) meant `alembic upgrade head` failed on `002`'s
  `ALTER TABLE`, every time, on every machine, silently, since it was written. Fixed now (commit
  `0a2482a` on `schema-003`) in both the migration and `orm.py`'s `server_default`. **Next
  concrete step on the Mac: `alembic upgrade head` (should now clear `002` cleanly), then
  `alembic revision --autogenerate -m "..." --rev-id 003`, review, apply.** That's the actual
  next step — not "design the GraphQL schema" (substantially done in chat) or "run the real
  experiment" (done).
- **Sequencing correction:** the original plan was 003 lands *before* `types.py`. That's still
  right — but the SDL got designed in parallel/ahead of 003 finishing, informed 003 (the
  `candidate_chains` decision came *from* the GraphQL design work), and now 003 needs to catch up
  and land before `types.py` implementation starts for real.

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

## Data generation — the real Bio Discovery run (dropped from this doc; restored 2026-07-04)

`docs/catalog-seed-plan.md` TODO #6 flagged this as a "(later)" item and this handoff lost it.
Restoring it as a **first-class next step**, because it gates good GraphQL design: we have no real
candidate output yet — only synthetic `sample_data/her2_nanobody_sample.csv`. Everything about
`Candidate` / `scores` is being designed against a guess at what a real export looks like.

**Goal:** design and run one real experiment in Amazon Bio Discovery, export the result CSV, and
study it. First real data; replaces synthetic.

- **Target:** HER2 extracellular domain, PDB `1N8Z` (the HER2/Herceptin-Fab co-crystal; chosen
  over 1S78 as the better-studied structure — the whole reason this project exists).
- **Budget:** free trial is **5 Experimental Units/month** — the run has to be designed to fit,
  so recipe choice + number of designs is a real constraint, not a free parameter.
- **To decide together (this is a design session, not a scripted task):** which recipe/modules,
  run params (num designs, hotspots, design loops…), and which output columns we *expect* — then
  cross-check the export's actual headers against the 33-entry catalog.
- **Payoff, and why it comes before finalizing GraphQL/SQL:** (a) validates the CSV importer
  against reality; (b) flips `provenance` from `INFERRED` → `AWS_CONFIRMED` for the columns a real
  run actually emits, and corrects any inferred direction/unit that's wrong; (c) gives us real
  `Candidate` rows to design the `Candidate`/`scores` GraphQL queries against — instead of
  guessing the shape, we query the real thing.

## SQL schema ≠ GraphQL schema (design principle)

The GraphQL schema is the **apparent representation** — the shape a frontend/consumer sees, and the
actual point of this project's "GraphQL-first" ordering. The SQL schema is free to differ: designed
for retrieval and storage sanity, **not** required to be a 1:1 mirror of the GraphQL types. A
`candidate_scores` join table, a `scores` JSONB column, and a resolver that stitches them into one
tidy `Candidate.scores: [ScoredMetric]` GraphQL field can all coexist — the resolver is exactly the
seam that lets the two schemas diverge. Design the GraphQL shape for the consumer first; let it
*inform* the SQL (see step 5 below), but don't collapse them into the same table layout by reflex.

## TODO (deferred, good enough for the portfolio piece as-is)

- **`Experiment.params` stays opaque `JSON` for now.** It's stored as `params` JSONB (validated:
  De Novo Design vs Directed Evolution have entirely disjoint param sets — a bag, not columns) and
  exposed in GraphQL as an opaque `JSON` scalar. Display queries work (`experiment(id) { params }`);
  cross-experiment filtering by a param value (e.g. "all runs where model=esm") does **not** without
  Postgres JSON-path operators — acceptable, since display >> param-filtering for a demo.
  **Future symmetry, only if a param catalog gets built:** the deferred "surface required inputs
  from the README survey" idea (see §"Known design gap" / point 2) *is* a param catalog — a
  `ModuleInput`/`ParamSpec` table. If that lands, `params` can get the exact `ScoreEntry` treatment:
  project it as `[ParamEntry { key, value, spec }]`, resolved and queryable, degrading to
  `{key, value}` for uncataloged params — the same pattern `scores`→`ScoreEntry` uses. Until there's
  a catalog to interpret against, opaque JSON is the honest representation. (Diagram annotated in
  `docs/schema-erd.md`.)

## Next steps, in order (superseded — see STATUS UPDATE above; kept for history)

1. ~~Merge `catalog-seed` → `main`~~ ✅ done (PR #3, then PR #4 for the sample-run chapter).
2. ~~Design + run the real Bio Discovery experiment~~ ✅ done — `docs/first-run-findings.md`.
3. ~~Design the GraphQL schema~~ — substantially done in a PC chat session (types, `ScoreEntry`,
   the `Chain`/`candidate_chains` decision); not yet transcribed into a committed SDL file.
4. **← actually next:** finish migration `003` — ORM models are complete (branch `schema-003`),
   run `alembic revision --autogenerate`, hand-add the `ALTER TYPE variantkind ADD VALUE
   'transform'` (autogenerate won't emit it — see the comment on `VariantKind` in `orm.py`),
   review, apply, PR to `main`.
5. **Then:** write `app/graphql/types.py`/`schema.py` for real, using the diagram in
   `docs/schema-erd.md` as the spec, with `YOUR TURN` markers for the meaningful resolver logic
   (Ryan is typing this one himself, learning Python/Strawberry GraphQL deliberately).
