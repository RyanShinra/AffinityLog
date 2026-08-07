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
  transformOf: Metric
  benchmarkResults: [BenchmarkResult!]!
}

type Module {
  name: String!
  moduleType: ModuleType!
  functions: [String!]!
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

**A rename is still pending underneath this.** The storage layer has the same problem in reverse:
`candidate_chains.chain` holds the role while `candidate_chains.sequence` holds the actual chain. The
column and Postgres type keep their current names for now — `SAEnum(ChainRole, name="chain")` pins the
type so no migration was needed for the API rename. Migration 007 renames the column to `role` and the
type to `chainrole`, and removes that pin. Until then the ORM attribute is `chain` while the GraphQL
field is `role`, which is why `Chain.from_orm` maps `role=row.chain`.

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

---

## The coupling nothing currently guards

The three interface strings — `'antibody-target complex'`, `'antibody only (H/L pairing)'`,
`'single chain (no interface)'` — must match **exactly** in three places:

1. the `CASE` arms in `sql/candidate_summary.sql` (and its snapshot in migration 004)
2. the `variant` values of the nine INTERFACE rows in `seed/catalog.json`
3. this enum

Nothing verifies this. Rewording a `CASE` arm would silently stop those three keys resolving, with no
test failing and no error raised — the scores would simply lose their meaning.

The guard, modelled on `scripts/check_view_migration.py`, is three assertions:

1. the string literals in the view's `CASE` == the enum's values *(the one with teeth — it fails on
   the edit, before any data proves it)*
2. every distinct `interface_kind` in the live view is an enum member
3. every INTERFACE metric's `variant` is an enum member (a subset — three of four)

This should be written **before** the `ScoreEntry` resolver, not after.

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
