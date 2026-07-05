# AffinityLog — Schema: SQL ERD vs. GraphQL apparent representation

Two views of the same data, side by side:

- **SQL ERD** — how it's *stored* (normalized, integrity-first). `orm.py` + migrations `001`–`002`,
  plus what migration `003` adds (`BenchmarkDataset`, `BenchmarkResult`, `Module.version/license`,
  and now the `candidate_chains` child table — see below).
- **GraphQL** — how it's *served* (the "apparent representation" a consumer sees). Deliberately
  **not** a 1:1 mirror — see the divergence table at the bottom.

Both are the source of truth for their layer; the resolver is the seam that lets them differ.

> **Change (2026-07-04):** `Candidate.fasta_sequence` (one `/`-joined text blob) is replaced by a
> `candidate_chains` child table — one row per chain (H/L/T), because a candidate may have 0–many
> targets and 0–1 light chains depending on format (nanobody vs IgG/scFv vs protein-protein dock).
> Nothing about "nanobody" or "exactly one target" is baked in. See `docs/first-run-findings.md` §8.

## SQL — how it's stored

```mermaid
erDiagram
  CONCEPTS {
    uuid id PK
    string name UK
  }
  MODULES {
    uuid id PK
    string name UK
    enum module_type
    string repo_url
    string version "migration 003"
    string license "migration 003"
  }
  METRICS {
    uuid id PK
    uuid module_id FK
    string column_key
    enum value_type
    enum direction
    enum variant_kind
    string variant
    uuid concept_id FK
    json transform
    enum provenance
  }
  BENCHMARK_DATASETS {
    uuid id PK "migration 003 (planned)"
    string name UK
    text description
    int seed_antibody_count
  }
  BENCHMARK_RESULTS {
    uuid id PK "migration 003 (planned)"
    uuid metric_id FK
    uuid benchmark_dataset_id FK
    string property
    float spearman_correlation
    float auroc
    json strata
  }
  PROJECTS {
    uuid id PK
    string name
  }
  TARGETS {
    uuid id PK
    string name
    string pdb_id
  }
  RECIPES {
    uuid id PK
    string name
    string recipe_type
  }
  EXPERIMENTS {
    uuid id PK
    string name
    uuid project_id FK
    uuid recipe_id FK
    uuid target_id FK
    json params
    string source_filename
  }
  CANDIDATES {
    uuid id PK
    uuid experiment_id FK
    string sequence_id
    json scores
    text annotation
  }
  CANDIDATE_CHAINS {
    uuid id PK "migration 003 (new)"
    uuid candidate_id FK
    enum chain "H / L / T"
    text sequence
    int ordinal "disambiguates repeated labels"
  }
  ARTIFACTS {
    uuid id PK
    uuid candidate_id FK
    string kind
    string uri
  }

  CONCEPTS |o--o{ METRICS : "categorizes"
  MODULES ||--o{ METRICS : "emits"
  METRICS ||--o{ BENCHMARK_RESULTS : "validated against (planned)"
  BENCHMARK_DATASETS |o--o{ BENCHMARK_RESULTS : "measured on (planned)"
  MODULES }o--o{ RECIPES : "recipe_modules"
  PROJECTS |o--o{ EXPERIMENTS : "groups"
  TARGETS |o--o{ EXPERIMENTS : "designed against"
  RECIPES |o--o{ EXPERIMENTS : "runs as"
  EXPERIMENTS ||--o{ CANDIDATES : "produces"
  CANDIDATES ||--o{ CANDIDATE_CHAINS : "has chains"
  CANDIDATES ||--o{ ARTIFACTS : "has"
```

## GraphQL — how it's served (apparent representation)

Nodes = types, arrows = fields that reference another type. Dashed = a projection/edge with **no
backing FK** (synthesized by a resolver). `ScoreEntry` is the standout: **a type backed by no table
at all** — the resolver builds it per-query from a `scores` JSONB entry + a catalog `Metric` lookup.

**Convention:** this is an *edges-only* view — scalar fields (`id`, `tier`, `recommendation`,
`sequenceId`, `pdbId`, …) exist on every type but aren't drawn, since they aren't references. The
one scalar worth flagging is `Experiment.params` (the `[params: JSON]` node below): it's **present**
in the API, just **opaque** — a JSON blob we don't interpret, because (unlike `scores`, which has the
`Metric` catalog to resolve against) there's no param catalog yet. See the TODO in
`graphql-schema-handoff.md`. Note `scores` is *not* absent here — it's the `→ ScoreEntry` edge (the
interpreted projection); only its raw JSONB backing lives SQL-side.

```mermaid
graph LR
  Q([Query])
  Q --> C[Candidate]
  Q --> E[Experiment]
  Q --> Mo[Module]
  Q --> Me[Metric]

  C -->|chains| CC[ChainSequence]
  C -.->|scores| SE[ScoreEntry - no table]
  C -->|experiment| E
  C -.->|target hoist| T[Target]
  C -->|artifacts| A[Artifact]

  SE -.->|metric, resolved| Me
  SE --> CH{{Chain enum}}
  CC --> CH

  Me -->|module| Mo
  Me -->|concept| Co[Concept]
  Me -->|benchmarkResults| BR[BenchmarkResult]
  Me -.->|transformOf| Me

  E -->|target| T
  E -->|recipe| R[Recipe]
  E -->|candidates| C
  E -.->|params| PJ["params: JSON<br/>opaque, uninterpreted"]
  Mo -->|metrics| Me
```

## Where they diverge (and why)

| SQL (stored) | GraphQL (served) | Why |
|---|---|---|
| `candidates.scores` — one JSONB column | `Candidate.scores: [ScoreEntry]` | Flexible blob stored; interpreted list projected. `ScoreEntry` has no table. |
| `metrics` & `candidates` — no FK between them | `ScoreEntry.metric` resolves the link at query time | Resolver marries JSONB key ↔ catalog row; edge doesn't exist in SQL. |
| `candidate_chains` rows (H/L/T) | `Candidate.chains: [ChainSequence]` | Roughly 1:1 — the one place they nearly match. |
| candidate → experiment → target (2 FK hops) | `candidate.target` (1 hoisted field) | Apparent rep flattens the join into a convenience edge. |
| `recipe_modules` M:N (+ wiring) | `recipe.modules` only; DAG hidden | Arbitrary graphical wiring is stored, not surfaced. |
| every table full-CRUD | query-all + annotate-only mutation | Writes are REST (CSV import); GraphQL is reads + a sliver. |
| `experiments.params` JSONB | `Experiment.params: JSON` (opaque) | Per-recipe config varies wildly (De Novo vs Directed Evolution) — a bag, not columns. |

Full SDL sketch lives in the design discussion / `graphql-schema-handoff.md`; this file is the
picture. Renders on GitHub natively and in VS Code with any mermaid extension.
