# AffinityLog

A portfolio backend API that ingests and serves Amazon Bio Discovery antibody design experiment data.

---

## Background

At the AWS Summit in June 2026, I saw a demo of **Amazon Bio Discovery** — AWS's computational
antibody engineering platform. It reconnected me to a Chemical Engineering background I hadn't
used professionally, and I signed up for the free trial (5 Experimental Units/month) to run a
real antibody design experiment against the **HER2 extracellular domain (PDB 1S78)**.

AffinityLog is the backend that will ingest and serve that experimental output. It's also a
portfolio project alongside [MyTower](https://github.com/RyanShinra/MyTower), demonstrating
FastAPI, Strawberry GraphQL, async SQLAlchemy, Docker, and GitHub Actions CI.

---

## Why CSV import (not an API integration)

Amazon Bio Discovery has **no public API, no boto3 SDK, and no REST endpoints** as of June 2026.
It's a UI-only, no-code platform. The only way to get data out is to export results as CSV from
the Bio Discovery console. AffinityLog imports those CSVs via a multipart REST endpoint and stores
them in PostgreSQL.

---

## Current data status

> **This repo currently contains synthetic placeholder data only.**
> `sample_data/her2_nanobody_sample.csv` is a generated fixture with 35 synthetic nanobody
> candidates, realistic score distributions, and plausible sequence IDs — not real experimental
> output. Real Bio Discovery results will be dropped in when the experiment is run.

---

## Architecture: two-headed API

AffinityLog exposes two API surfaces on one FastAPI app:

| Surface | Path | Purpose |
|---|---|---|
| **REST** | `/experiments`, `/experiments/{id}/candidates/import` | CSV file upload (multipart) |
| **GraphQL** | `/graphql` | All queries, filtering, comparison, mutations |

**Why the split?** Strawberry GraphQL (and most GraphQL servers) have no native multipart file
upload support. Adding it for a single import endpoint would mean extra tooling and complexity.
REST handles what REST does best; GraphQL handles richly-shaped queries that a frontend will consume.

---

## Data model

### Experiment
| Field | Notes |
|---|---|
| `id` | UUID |
| `name` | Human label |
| `recipe_name` | Bio Discovery workflow name |
| `target_name` | e.g. "HER2 extracellular domain (PDB 1S78)" |
| `target_pdb_id` | e.g. "1S78" |
| `source_filename` | Imported CSV filename |
| `notes` | Free text |
| `created_at` | |

### Candidate
| Field | Notes |
|---|---|
| `sequence_id` | Bio Discovery's candidate identifier |
| `fasta_sequence` | Amino acid sequence |
| `binding_affinity_kd` | Nanomolar; lower = tighter binding |
| `humanness_score` | 0–1; higher = more human-like (BioPhi-style) |
| `aggregation_propensity` | Aggrescan3D-style; lower = lower manufacturing risk |
| `annotation` | Free-text note (writable via GraphQL mutation) |
| `raw_scores` | JSONB — all other CSV columns, keyed by original header |

**Why normalized columns + JSONB:** Different Bio Discovery recipes produce different score
columns. Three metrics are stable enough across antibody design to deserve indexed columns.
Everything else is captured in `raw_scores` (JSONB) and exposed via GraphQL's JSON scalar —
no schema changes needed when a new recipe emits different columns.

---

## Running locally

```bash
# 1. Clone and configure
cp .env.example .env

# 2. Start Postgres + app (migrations run automatically)
docker compose up --build

# 3. Open the GraphQL playground
open http://localhost:8000/graphql

# 4. REST API docs
open http://localhost:8000/docs
```

---

## Importing experiment data

### Step 1: Create an experiment

```bash
curl -X POST http://localhost:8000/experiments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "HER2 Nanobody Run 1",
    "recipe_name": "BoltzGen nanobody design",
    "target_name": "HER2 extracellular domain (PDB 1S78)",
    "target_pdb_id": "1S78"
  }'
# Returns: {"id": "<uuid>", ...}
```

### Step 2: Import candidates CSV

```bash
EXPERIMENT_ID="<uuid from step 1>"

curl -X POST "http://localhost:8000/experiments/${EXPERIMENT_ID}/candidates/import" \
  -F "file=@sample_data/her2_nanobody_sample.csv"
# Returns: {"rows_processed": 35, "rows_imported": 35, "rows_skipped": [], ...}
```

---

## Example GraphQL queries

### List experiments

```graphql
query {
  experiments {
    id
    name
    recipeName
    candidateCount
  }
}
```

### Filter candidates by affinity

```graphql
query {
  candidates(
    filter: { maxBindingAffinityKd: 5.0, minHumannessScore: 0.8 }
    sortBy: BINDING_AFFINITY_KD
    direction: ASC
  ) {
    sequenceId
    bindingAffinityKd
    humannessScore
    aggregationPropensity
    rawScores
  }
}
```

### Top candidates (composite ranking)

```graphql
query {
  topCandidates(
    experimentId: "<uuid>"
    limit: 10
    criteria: { weightBindingAffinity: 1.5, weightHumanness: 1.0, weightAggregation: 0.8 }
  ) {
    sequenceId
    bindingAffinityKd
    humannessScore
  }
}
```

### Annotate a candidate

```graphql
mutation {
  annotateCandidate(id: "<candidate-uuid>", annotation: "Promising — low Kd, high humanness") {
    id
    annotation
  }
}
```

---

## Running tests

```bash
pip install -e ".[dev]"
pytest -v
```

Tests use [testcontainers](https://testcontainers.com/) to spin up a real Postgres automatically.
Set `TEST_DATABASE_URL` to skip testcontainers and point at an existing instance.

---

## Security note

Authentication and authorization are intentionally absent — this is a local portfolio demo.
Before any real deployment, add authn (OAuth2/JWT) and scope the CORS `allow_origins` to
specific frontend domains.

---

## Next steps

- [ ] Run real Bio Discovery experiment; drop CSV into `sample_data/`; update column mapping if needed
- [ ] ECS deployment (Dockerfile and `/health` endpoint are ready)
- [ ] Svelte/React frontend (GraphQL API is frontend-ready; no backend changes needed)
