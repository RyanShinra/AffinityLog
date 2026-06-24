# AffinityLog — Claude Code Project Memory

Read this before contributing in any session. This is a multi-machine project (Mac + PC).
A new session should be able to contribute without re-litigating the decisions below.

---

## What this is

A portfolio backend API that ingests Amazon Bio Discovery antibody/nanobody design exports
and exposes them via GraphQL. Built to showcase the owner's return to a Chemical Engineering
background after the AWS Summit (June 2026), where Amazon Bio Discovery was demoed.

**Owner:** Senior backend engineer (TypeScript/Node.js primary, Python developing).

---

## The no-API constraint (important — don't try to work around it)

Amazon Bio Discovery has **no public API, no boto3 support, no REST/SDK** as of June 2026.
It is a UI-only, no-code product. The only integration point is exporting results as CSV
from the Bio Discovery console. Do not attempt to call, scrape, or reverse-engineer an API
— none exists. All external data enters AffinityLog through manual CSV uploads.

---

## Two-headed API design (REST + GraphQL)

This is an intentional architectural split, not an oversight:

- **REST** (`POST /experiments`, `POST /experiments/{id}/candidates/import`): CSV file
  uploads. GraphQL has no native multipart file upload support in Strawberry without
  extra tooling we chose not to add for a single endpoint.
- **GraphQL** (`/graphql`): all queries, filtering, sorting, comparison, and mutations
  (annotations). Richer for a future frontend to consume.

Both live on the same FastAPI app. CORS is open (`*`) — this is a local portfolio demo.

---

## Data model rationale

Two core tables: `experiments` and `candidates`.

**Why normalized columns + JSONB:**
Bio Discovery "recipes" (chained computational workflows) produce different score columns.
Three metrics are common enough across antibody design to deserve first-class columns with
indexes: `binding_affinity_kd` (nM, lower = tighter), `humanness_score` (0–1, BioPhi-style),
`aggregation_propensity` (Aggrescan3D-style). Everything else goes into `raw_scores` (JSONB),
queryable via GraphQL's JSON scalar. This avoids schema changes when a new recipe emits
different score columns.

---

## Current data status

**SYNTHETIC PLACEHOLDER DATA ONLY.** No real Bio Discovery experiment has been run yet.
`sample_data/her2_nanobody_sample.csv` is a generated fixture with plausible sequence IDs,
synthetic FASTA-like sequences, and realistic score distributions. When a real export lands:

1. Drop the real CSV into `sample_data/`.
2. Update column mapping in `app/importer/column_mapping.py` if column names differ.
3. That should be the only required change — schema is designed to absorb column variation.

---

## Tech stack

| Layer | Choice |
|---|---|
| Framework | FastAPI |
| GraphQL | Strawberry (mounted on FastAPI) |
| ORM | SQLAlchemy 2.0 async |
| DB driver | asyncpg |
| DB | PostgreSQL 16 |
| Migrations | Alembic |
| Tests | pytest + pytest-asyncio + testcontainers |
| Lint/format | ruff + black |
| Type-check | mypy (strict) |
| CI | GitHub Actions (lint → test → build) |

---

## ECS deployment (deferred)

Docker and config are ECS-ready (`/health` endpoint, env-var–driven config, correct port
binding), but no ECS infrastructure exists yet. Deploy target for this sprint is local
Docker only. Do not add a deploy step to CI until the next sprint.

---

## Non-obvious conventions

- `raw_scores` stores extra CSV columns as `dict[str, str]` (raw string values from the CSV,
  not coerced). Downstream callers handle type coercion if needed.
- Alembic migration file: `migrations/versions/001_initial_schema.py`. Run `alembic upgrade head`
  before starting the server (docker-compose does this automatically).
- The GraphQL context passes the SQLAlchemy engine (not a session) so each resolver opens
  its own session — avoids session-lifetime issues with async GraphQL resolvers.
- Column name variants are mapped in `app/importer/column_mapping.py`. When a real Bio
  Discovery export arrives with different headers, add variants there.
- Tests use testcontainers (pulls `postgres:16-alpine` at runtime) or `TEST_DATABASE_URL`
  env var if you want to point at an existing Postgres.

---

## Quick start

```bash
cp .env.example .env
docker compose up --build
# GraphQL playground: http://localhost:8000/graphql
# REST docs:         http://localhost:8000/docs
# Health:            http://localhost:8000/health
```
