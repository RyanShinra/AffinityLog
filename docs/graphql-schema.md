# GraphQL schema — the committed spec

**Status: current.** Written 2026-08-02, before any resolver existed, and reviewed as a document
rather than as code. Every count quoted below was measured against the loaded corpus on that date;
the queries that produced them are reproducible from `scripts/extract_score_keys.py` and the
`metrics` table.

This is the *apparent representation* — the shape a consumer sees. It is deliberately not a mirror
of the SQL schema. `docs/schema-erd.md` draws both side by side and tabulates where they diverge;
this file is the contract itself.

Its predecessor, `docs/graphql-schema-handoff.md`, records how the design was arrived at and flags
that the SDL "only exists in that conversation" as a real gap. This file closes that gap. Where the
two disagree, this one wins.

---

## What the corpus actually looks like

The schema's harder decisions all turn on four measurements, so they are stated up front:

| measurement | value |
|---|---|
| distinct score keys across all candidates | 200 |
| catalog identities those collapse to (chain suffix stripped) | 138 |
| rows in `metrics` | 144 |
| keys resolving on identity alone, no candidate context | **197** |
| keys needing the candidate's `interface_kind` to disambiguate | **3** |
| keys resolving to nothing | **0** |
| scores on a single candidate | 38 – 130 |
| identities measured on more than one chain | 37 |

The three interface-dependent keys are `boltz2.iptm`, `boltz2.protein_iptm` and
`boltz2.complex_ipde` — the finding in the 2026-07-31 entry of `docs/schema-stress-log.md`, reaching
the API surface. Every other key is a dictionary lookup.

Two further counts matter for how "known" a metric is:

| signal | meaning | rows |
|---|---|---|
| `provenance = AWS_CONFIRMED` | AWS documentation confirms what it means | 2 |
| `display_name != column_key` | a human wrote a real name for it | 28 |
| `notes IS NOT NULL` | a caveat exists that must travel with the number | 19 |

These are three different questions and the schema keeps them separate. `provenance` in particular
is **not** a curation flag — 142 of 144 rows are `INFERRED`, including every hand-curated one.

---

## SDL

```graphql
enum ChainRole { HEAVY LIGHT TARGET }
enum Direction { HIGHER_IS_BETTER LOWER_IS_BETTER NEUTRAL }
enum MetricValueType { FLOAT INT BOOL CATEGORICAL }
enum VariantKind { PARAMETER MODE SOURCE_MODEL COMPONENT TRANSFORM INTERFACE }
enum ModuleType { DESIGN SCORE DESIGN_AND_SCORE }

# The four arms of the CASE in sql/candidate_summary.sql. The first three are biology; the
# fourth is a data-quality state meaning "no chain rows were imported", not a kind of molecule.
enum InterfaceKind {
  ANTIBODY_TARGET_COMPLEX      # "antibody-target complex"     — a real binding interface
  ANTIBODY_ONLY_HL_PAIRING     # "antibody only (H/L pairing)" — no antigen present
  SINGLE_CHAIN_NO_INTERFACE    # "single chain (no interface)" — nothing to score against
  NO_CHAINS_RECORDED           # "no chains recorded"          — unreached in data; see below
}

type Query {
  candidate(id: ID!): Candidate
  candidates: [Candidate!]!
  experiment(id: ID!): Experiment
  experiments: [Experiment!]!
  modules: [Module!]!
  metrics: [Metric!]!
}

type Mutation {
  annotateCandidate(id: ID!, annotation: String!): Candidate!
}

type Candidate {
  id: ID!
  sequenceId: String!
  annotation: String
  interfaceKind: InterfaceKind!
  chains: [Chain!]!
  scores(module: String, chain: ChainRole, concept: String): [ScoreEntry!]!
  experiment: Experiment!
  target: Target          # hoisted through experiment — two FK hops flattened to one field
  artifacts: [Artifact!]!
}

# Backed by no table. Built per query from one entry in the candidates.scores JSONB bag,
# plus the catalog row that says what it means.
type ScoreEntry {
  key: String!            # the raw JSONB key, e.g. "boltz2.protein_iptm.H"
  value: String!          # raw text, exactly as stored — never coerced
  numericValue: Float     # null unless metric.valueType is FLOAT or INT
  chain: ChainRole        # null for the 101 identities carrying no chain suffix
  metric: Metric          # nullable — see "Decisions" below
}

type Chain {
  role: ChainRole!
  sequence: String!
  ordinal: Int!
}

type Metric {
  columnKey: String!
  displayName: String!
  curated: Boolean!       # computed, no column: displayName != columnKey
  valueType: MetricValueType!
  unit: String
  direction: Direction!
  variantKind: VariantKind
  variant: String
  notes: String
  propertyCategories: [String!]!
  module: Module!
  concept: Concept
  transformOf: Metric  # NOT BUILT: nothing populates transform_of_metric_id yet
  benchmarkResults: [BenchmarkResult!]!
}

type Module {
  name: String!
  moduleType: ModuleType!
  functions: [ModuleFunction!]!  # Adding an enum to cover this (WIP)
  repoUrl: String
  description: String
  version: String
  license: String
  metrics: [Metric!]!
}

type Concept {
  name: String!
  label: String!
  description: String
}

type Experiment {
  id: ID!
  name: String!
  params: JSON!           # opaque by design — no param catalog exists to interpret it against
  sourceFilename: String
  notes: String
  project: Project
  recipe: Recipe
  target: Target
  candidates: [Candidate!]!
}

type Recipe {
  name: String!
  recipeType: String
  author: String
  version: String
  modules: [Module!]!     # the module set only; DAG edges are not stored — see recipe-topology-note.md
}

type Target  { name: String!, pdbId: String }
type Project { name: String!, notes: String }
type Artifact { kind: String!, uri: String! }

type BenchmarkResult {
  property: String!
  n: Int!
  spearmanCorrelation: Float!
  auroc: Float
  auprc: Float
  precisionTop5: Float
}
```

---

## Decisions

### `Chain` names the object; `ChainRole` names the enum

Originally the enum was `Chain` and the object type was `ChainSequence`, which left `candidate.chains`
returning a list of `ChainSequence` while `Chain` meant something else entirely — and in Python put
`orm.Chain` beside a `chains` list of `CandidateChain` rows, close enough to read as SQL method
chaining.

`ChainRole` is the accurate name for the enum, and deliberately not `ChainType`: HEAVY and LIGHT are
genuine structural classes of antibody chain, but TARGET is the antigen and not an antibody chain at
all. The one word covering all three is the *part played* in the assembly. Renaming it also freed
`Chain` for the object, which is what a chain actually is — a sequence with a role and an ordinal.

**The storage layer had the same problem in reverse**, and migration 007 fixed it:
`candidate_chains.chain` held the role while `candidate_chains.sequence` held the actual chain. The
column is now `role` and the Postgres type `chainrole`, so `Chain.from_row` maps `role=row.role` and
the two layers agree.

The rename reached the API first, one commit ahead of the database. During that gap the Python class
was renamed while the column was not, held together by `SAEnum(ChainRole, name="chain")` pinning the
type name so the API rename needed no migration. That pin is gone. The technique is worth remembering
— it decouples an application-layer rename from a schema change — but nothing in the tree depends on
it now.

### `interfaceKind` is an enum, and it has four members

The three biological arms will not grow — an interface is scored against a target, against the
antibody's own light chain, or against nothing. The fourth member is different in kind.

`'no chains recorded'` is the first branch of the view's `CASE`, and it is a guard rather than a
classification: the view LEFT JOINs `candidate_chains`, so a candidate with no chain rows still
produces a group, and `bool_or` over zero rows returns NULL rather than false. Without that branch
such a row falls through to `ELSE` and is silently mislabelled as a single chain.

All 14 candidates currently have chains, so the branch is **reachable but unreached**. It is in the
enum anyway, because the row that would hit it is a row whose chain import failed — exactly the case
where the API should say "unknown" rather than raise on an enum conversion.

Note the asymmetry this creates, which is correct: the catalog holds **three** INTERFACE metric rows
per interface-dependent column, not four. A `NO_CHAINS_RECORDED` candidate's `boltz2.iptm` therefore
resolves to no metric at all — handled by the next decision.

### `ScoreEntry.metric` is nullable

Measured today, every one of the 200 keys resolves. But that is tautological: the catalog was derived
*from* this corpus by `scripts/extract_score_keys.py`, so of course it covers it. The guarantee is an
artifact of how the catalog was built, not a property of the data model.

A non-null `Metric!` would also fail destructively. In GraphQL, a non-null field that cannot resolve
propagates the null upward — one unrecognized key would blank out the entire candidate.

More importantly, an unresolvable score is **information, not an error**: it says the column did not
come from Bio Discovery. That is a fact worth surfacing rather than swallowing, and it is the real
reason the field is nullable.

(`_export.tier` and `_export.recommendation` — the two prefix-less keys, one of which is AI-written
prose rather than a measurement — *do* have skeleton rows and so do resolve. `scripts/seed_metric_skeleton.py`
registers them deliberately, so nothing is lost silently. `module.name == "_export"` is the visible
marker that they are not really module outputs.)

### `scores` takes optional filters

At 130 entries on the largest candidate, a flat list is unwieldy. All three arguments are optional
and independent; passing none returns everything, which is the common case.

### `curated` is computed, with no column behind it

`display_name != column_key` is the only reliable signal that a human attached meaning to a metric —
`provenance` does not distinguish curated rows (see the table above). Exposing the comparison as a
named boolean stops every consumer from reimplementing it, and it is a clean illustration of why the
GraphQL layer exists: a field that is real to the client and absent from the database.

### `value` is never coerced

Scores are stored as text, uncoerced, and are served that way. `numericValue` is populated only where
the catalog says the metric is numeric, and is null otherwise — a separate field rather than a
best-effort cast on `value`. Silent coercion is how a categorical that happens to look like a number
becomes a number forever.

### `annotateCandidate`: the empty string clears

`annotation` is `String!` and `candidates.annotation` is nullable, so the API needs a rule for
clearing. `""` stores NULL. A nullable argument was considered and rejected: a nullable argument
with no default is also omittable, so `annotateCandidate(id: "x")` would clear silently. Requiring
a string, and making the one string that is not an annotation mean "none", keeps the clear explicit.

A well-formed id matching nothing is a coded `NOT_FOUND`, distinct from the `BAD_USER_INPUT` a
malformed id raises. The return type is non-null, so null was never an option for either.

---

### Errors: deliberate ones speak for themselves, everything else is masked

A resolver that raises does not fail the request. Strawberry catches it, puts `str(exception)` into
the response's `errors` array, and nulls the field. No traceback is sent — but the *message* is, and
that turned out to matter. Measured against the real database before anything was done about it:

```
broken statement   →  (asyncpg.ProgrammingError) column "nonexistent_column" does not exist
                      [SQL: SELECT nonexistent_column FROM candidates]
database down      →  [Errno 111] Connect call failed ('127.0.0.1', 5999)
```

The full statement text, and the internal host and port. (Credentials do not travel — checked
specifically, with a password in the DSN.)

The rule now is that **an error is shown iff it carries a deliberate `code` extension**. Anything
raised on purpose sets one; nothing raised by SQLAlchemy, asyncpg, or an ordinary bug does. That
convention needs no new exception hierarchy, and it is what Apollo uses.

Two consequences worth knowing:

- A malformed id and a well-formed id matching nothing are **different answers**. The first returns
  `null` *and* an `errors` entry coded `BAD_USER_INPUT`; the second returns a plain `null`. Telling
  someone who sent garbage that their record simply is not there would be the wrong answer.
- Masking costs no diagnostics. The original error is logged to `strawberry.execution` before the
  message is replaced, so the client loses the detail and the server keeps it.

The policy lives in `app/graphql/errors.py`; `tests/test_error_masking.py` guards it, including an
assertion that the served schema actually registers the extension — the failure mode here is silent,
since masking that stops working breaks nothing and simply starts leaking again.

### `ScoreEntry` resolves against a catalog loaded once per request

Sizing it first, because the numbers decide the design (measured 2026-08-09):

| | |
|---|---|
| catalog rows | 144 (the `metrics` table is 152 KB in total) |
| `ScoreEntry` objects for one `{ candidates { scores } }` | **1132** |
| scores on a single candidate | 38 – 130 |

A query per key would be 1132 round trips for one request. The whole catalog is smaller than a
single candidate's score bag, so it is loaded **once per request** into a dict keyed by the same
identity `decompose()` produces — `(module, column_key, variant_kind, variant)` — and each of the
1132 lookups becomes a dict hit. Two queries total: one for the catalog, one for the interface kinds
below.

It is cached on the `Context` rather than at module level. The catalog only changes on a reseed, so a
process-wide cache is tempting, but it buys a staleness window and an invalidation story in exchange
for one query per request, which is not a trade worth making at this size.

### Which catalog row a key means is a two-tier question

197 of the 200 keys carry their whole identity. Three do not, and the difference is not a quirk — it
is the ipTM finding reaching the API.

`decompose("boltz2.protein_iptm")` yields the identity `(boltz2, protein_iptm, None, None)`. **No
such catalog row exists.** The three rows that do exist all carry `variant_kind = INTERFACE` and
differ by `variant`, which holds the candidate's interface kind. The key cannot name which one
applies, because the discriminator is a property of the *candidate* — which chains went into the
fold — and not of the key.

So the lookup is explicitly two-tier, and **the catalog is asked which tier applies** rather than the
resolver trying one and retrying on failure:

    kinds = <variant_kinds_of_column[(module, column_key)], or the empty set>
    if INTERFACE in kinds:  identity += (INTERFACE, interface_kind_of_this_candidate)
    else:                   identity is what decompose() returned

The retry-on-miss shape — look up, and if it misses try again qualified by interface kind — is
shorter and works today. It is not used, for two reasons. It reads as though interface-qualification
were a general fallback when it is specific to one `VariantKind`, so the natural way to extend it is
to stack more retries. And it is only correct while no `(module, column_key)` has both a variant-less
row and an INTERFACE row: if one ever did, tier one would hit and the interface rows would never be
consulted, silently returning the wrong meaning.

That invariant holds — measured, only four keys have multiple rows and none has a variant-less
sibling — and it is now **checked** rather than merely true. `app/catalog/invariants.py` fails the
seed if any heading carries two axes, or a variant-less row beside INTERFACE rows; both seeders call
it before committing. That is a stronger position than when this section was written, when the
invariant rested entirely on `scripts/seed_metric_skeleton.py` skipping on `(module, column_key)`
rather than on full identity.

So the *correctness* case against retry-on-miss is largely answered, and what remains is the first
reason plus a caveat: a write-time check is not a schema constraint, and it binds only rows the
seeders write — a hand-written `INSERT` bypasses it. Asking which tier applies cannot go wrong that
way at all. The structural version, which would make a two-axis heading unrepresentable, is written
up in [`metric-heading-normalization.md`](metric-heading-normalization.md).

### `numericValue` parses defensively, even though nothing currently fails

Of the 995 values whose metric says FLOAT or INT, **995 parse.** That is not the reassurance it
appears to be: `scripts/seed_metric_skeleton.py` *inferred* `value_type` from these very strings, so
the agreement is an artifact of how the types were assigned, exactly like "197 of 200 keys resolve".

Values that would not parse are already in the corpus — `"<40"`, `"-"` — currently sitting under
CATEGORICAL metrics. Correcting one of those to FLOAT, or importing a run with a censored value in a
numeric column, produces an unparseable value immediately.

So `numericValue` is `float()` inside a try/except, `None` on failure, and populated only when the
catalog says the metric is numeric. `value` always carries the raw string regardless, so nothing is
lost when the coercion declines.

### `benchmarkResults` and `transformOf` ship empty — for different reasons

Both are in the schema so the shape is right, and both return nothing. Neither blocks anything. But
they are empty in quite different senses, which is worth being precise about (measured 2026-08-09:
0 benchmark_results, 0 benchmark_datasets, 0 TRANSFORM metrics, 0 lineage links).

**`benchmarkResults` — the data exists, the loader does not.** The scrape is already done and in the
repo: `seed/raw/module_evaluation_details.json` holds 190 rows whose fields map essentially
one-to-one onto `BenchmarkResult` (property, spearman, stars, auroc, auprc, precisionTop5, n,
precisionTop5NullDist, strata), and `module_evaluation_catalog.json` holds 33 module entries with
version, license and transform. Populating it is a seeder in the style of the others, not new
collection — the terms-of-service review that cleared the scrape is recorded in
`graphql-schema-handoff.md`.

Worth doing eventually because it is the layer that says how much a metric is *worth*, not just what
it means. The catalog currently tells a client that higher ipTM is better and attaches a caveat;
benchmarks would add that a given metric's AUROC against titer is 0.647, which is the difference
between "0.79 beats 0.40" and "treat this as weak evidence". Given that the project exists to stop a
raw number misleading someone, that is a natural next layer.

Known gap in the source: most catalog rows still carry `"transform": null` even where the UI showed a
populated panel, so that axis is incomplete. The 190 benchmark rows are the solid part.

**`transformOf` — there is nothing to load.** The raw vs `-transformed` distinction came from AWS's
Module Evaluation *documentation*; none of the 200 keys in the actual run exports is a transformed
sibling. Zero instances is a fact about this corpus rather than a loading gap, and it would only
change if the catalog were seeded from the scrape *and* such a metric appeared in a run.

## The coupling, and the guard that now covers it

The three interface strings — `'antibody-target complex'`, `'antibody only (H/L pairing)'`,
`'single chain (no interface)'` — must match **exactly** in three places:

1. the `CASE` arms in `sql/candidate_summary.sql` (and its snapshot in migration 004)
2. the `variant` values of the nine INTERFACE rows in `seed/catalog.json`
3. this enum

Rewording a `CASE` arm would otherwise stop those three keys resolving, with no exception raised —
the scores would simply lose their meaning, which is the one thing the catalog exists to provide.
`scripts/check_view_migration.py` does not cover it: that compares the view's output **column
aliases**, so a reworded `THEN` literal passes it untouched.

**This is now guarded** by `tests/test_interface_kind.py`, written before the `ScoreEntry` resolver
as this section originally asked. It asserts the `CASE` arms in the `.sql` file *and* in migration
004 both equal the enum's values in order, and that every INTERFACE `variant` in `seed/catalog.json`
is an enum member — covering the three real arms but not `NO_CHAINS_RECORDED`, which is a
data-quality state the catalog should never carry. The parse anchors on `THEN`/`ELSE` results so the
chain labels inside the conditions (`'TARGET'`, `'LIGHT'`) are not mistaken for interface kinds.

It needs no database, and that is deliberate rather than a limitation: every source is a file in the
repo, and checking the `.sql` *and* the migration closes the chain end to end, since the migration is
what actually creates the view in any given database. The originally-proposed third assertion —
every distinct `interface_kind` in the *live* view is an enum member — was skipped on purpose: no
candidate in the corpus reaches `no chains recorded`, so it could only ever prove three of the four
arms, making it the weakest of the checks rather than the strongest.

What remains uncovered is narrow: a view altered by hand in a running database, diverging from both
files. Nothing in the workflow does that.

---

## Deliberately absent

- **Pagination / Relay connections.** Nine experiments, 14 candidates. Plain lists are the honest
  shape at this size; adding cursors would be ceremony, not capability.
- **Full CRUD.** Writes are REST (multipart CSV upload — Strawberry has no native multipart support
  without tooling this project chose not to add for one endpoint). GraphQL gets reads plus
  `annotateCandidate`.
- **Recipe DAG edges.** `Recipe.modules` exposes the module set; the wiring between them is not
  stored yet. See `docs/recipe-topology-note.md`.
- **`Experiment.params` interpretation.** Served as an opaque `JSON` scalar. Unlike `scores`, there
  is no param catalog to resolve it against; if one is ever built, `params` can get the same
  treatment `scores` gets here.
- **Benchmark data.** `Metric.benchmarkResults` is in the schema and will return `[]` — the
  `benchmark_datasets` / `benchmark_results` tables were left unpopulated on purpose. The field is
  present so the shape is right when they are filled.

---

## Keeping this file honest

Strawberry can print the schema it actually builds. Once resolvers exist, the same trick
`check_view_migration.py` uses applies here: compare the printed SDL against the block above and fail
CI when they drift. Until then, this document is the spec and the code does not exist to contradict
it.
