# AffinityLog — Schema ERD

Current schema (`app/models/orm.py`, migrations `001`–`002`) plus what migration `003` adds.
`BenchmarkDataset` and `BenchmarkResult` are **planned, not yet built** — see
`docs/catalog-seed-plan.md` and the `YOUR TURN` blocks in `orm.py`.

```mermaid
erDiagram
  CONCEPTS {
    uuid id PK
    string name UK
    string label
    string description
  }
  MODULES {
    uuid id PK
    string name UK
    enum module_type
    enum_array functions
    string repo_url
    string description
    string version "migration 003"
    string license "migration 003"
    timestamp created_at
  }
  METRICS {
    uuid id PK
    uuid module_id FK
    string column_key
    string display_name
    enum value_type
    string unit
    enum direction
    string_array property_categories
    enum variant_kind
    string variant
    uuid concept_id FK
    json transform
    enum provenance
    timestamp created_at
  }
  BENCHMARK_DATASETS {
    uuid id PK "migration 003 (planned)"
    string name UK
    string version
    text description "incl. binarization methodology"
    int seed_antibody_count
    int antigen_count
    int max_variants_per_seed
    int mutation_strategy_count
    int assay_count
    timestamp created_at
  }
  BENCHMARK_RESULTS {
    uuid id PK "migration 003 (planned)"
    uuid metric_id FK
    uuid benchmark_dataset_id FK
    string property
    int n
    float spearman_correlation
    string spearman_significance
    float auroc
    float auprc
    float precision_top5
    float_array precision_top5_null_dist "p5, p95"
    float positive_ratio
    json strata "per antibody format"
    timestamp created_at
  }
  PROJECTS {
    uuid id PK
    string name
    text notes
    timestamp created_at
  }
  TARGETS {
    uuid id PK
    string name
    string pdb_id
    timestamp created_at
  }
  RECIPES {
    uuid id PK
    string name
    string recipe_type
    string author
    string version
    timestamp created_at
  }
  EXPERIMENTS {
    uuid id PK
    string name
    uuid project_id FK
    uuid recipe_id FK
    uuid target_id FK
    json params
    string source_filename
    text notes
    timestamp created_at
  }
  CANDIDATES {
    uuid id PK
    uuid experiment_id FK
    string sequence_id
    text fasta_sequence
    json scores
    text annotation
    timestamp created_at
  }
  ARTIFACTS {
    uuid id PK
    uuid candidate_id FK
    string kind
    string uri
    timestamp created_at
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
  CANDIDATES ||--o{ ARTIFACTS : "has"
```
