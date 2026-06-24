# AffinityLog — Claude Code Build Prompt

Paste everything below this line into Claude Code as the task prompt.

---

## Context

I'm a senior backend engineer (TypeScript/Node.js primary, Python developing) building a portfolio project tied to a real moment: I attended the AWS Summit in June 2026, saw a demo of Amazon Bio Discovery (AWS's computational antibody engineering platform), and it reconnected me to a Chemical Engineering background I hadn't used professionally. I have a 30-day free trial of Bio Discovery (5 Experimental Units/month, no credit card) and I'm using it to run a real antibody design experiment against the HER2 extracellular domain (PDB 1S78). This project is the backend that will eventually ingest and serve that experimental data.

**Important constraint**: Amazon Bio Discovery has no public API or SDK (no boto3 support, no REST API found in its documentation as of June 2026). It's a UI-only, no-code product. The only integration point is exporting results as CSV from the Bio Discovery console and importing them here. Do not attempt to call, scrape, or reverse-engineer a Bio Discovery API — none exists.

**I don't have a real CSV export yet.** I haven't run my first Bio Discovery experiment. Build against the documented CSV export shape (described below) plus a synthetic sample dataset, structured so swapping in a real export later requires no schema changes — at most a column-mapping tweak.

This is a sprint-scale project (1-2 weeks of part-time work), meant to be a credible, demo-able, interview-ready artifact alongside my other portfolio project (MyTower — github.com/RyanShinra/MyTower, also FastAPI + Strawberry GraphQL + Docker + ECS).

## Project name

**AffinityLog**. Use it for the repo, package names, etc.

## Goal

A backend API service that:
1. Ingests Bio Discovery experiment CSV exports (antibody/nanobody candidates with design scores).
2. Stores them in PostgreSQL with a schema that survives variation in score columns across different "recipes" (Bio Discovery's term for a chained computational workflow — different recipes produce different score columns).
3. Exposes a GraphQL API for querying, filtering, sorting, and comparing candidates across and within experiments.
4. Is architected so a Svelte or React frontend can be bolted on later without backend changes — this sprint is API-only, but don't paint it into a corner.

GraphQL doesn't handle file uploads cleanly (no native multipart support in most GraphQL servers, Strawberry included, without extra tooling I'd rather not add for a single import endpoint). So: **CSV import goes through a plain REST/FastAPI endpoint** (multipart form upload), and **everything else — querying, filtering, comparing, annotating — goes through GraphQL**. Two-headed API on one FastAPI app, both documented clearly in the README as an intentional split, not an oversight.

## Tech stack

- Python 3.12+
- FastAPI (REST endpoint for CSV import + serving the GraphQL app)
- Strawberry GraphQL
- PostgreSQL (async driver — asyncpg, via SQLAlchemy 2.0 async or raw asyncpg, your call, but be consistent)
- Docker, multi-stage Dockerfile (mirror the general shape of MyTower's, optimized build layer then slim runtime layer)
- docker-compose for local dev: app service + postgres service + named volume
- pytest for tests, ruff + black + mypy for lint/type-check
- GitHub Actions CI: lint, type-check, test, build image. **Do not add an ECS deploy step yet** — deployment target for this sprint is local Docker only. I'll deploy to ECS "in short order" after this sprint, so design the Dockerfile and config (env-var driven, a `/health` endpoint, correct port binding) so that step is low-friction later, but don't build it now.

## Data model

Two core entities, designed for column variability across recipes:

**Experiment**
- id
- name
- recipe_name (free text — Bio Discovery's workflow name, e.g. "BoltzGen nanobody design")
- target_name (e.g. "HER2 extracellular domain (PDB 1S78)")
- target_pdb_id (nullable, e.g. "1S78")
- created_at
- source_filename (the CSV that was imported, for traceability)
- notes (free text)

**Candidate**
- id
- experiment_id (FK)
- sequence_id (Bio Discovery's identifier for the candidate within the experiment)
- fasta_sequence (text)
- binding_affinity_kd (nullable float — lower is tighter binding; units nanomolar, store the unit alongside or normalize on import)
- humanness_score (nullable float — BioPhi-style immunogenicity score)
- aggregation_propensity (nullable float — Aggrescan3D-style manufacturing risk score)
- raw_scores (JSONB — every other column from the CSV that doesn't map to a normalized field above, keyed by original column name)
- created_at

Rationale: binding affinity, humanness, and aggregation propensity are common enough across antibody design recipes to deserve first-class normalized columns (so they're fast to filter/sort/index on), but different recipes will emit different additional score columns. Don't try to anticipate all of them — capture the rest in `raw_scores` and let the GraphQL layer expose JSONB querying for anything not normalized.

The CSV importer should be column-flexible: match known column name variants (e.g. "Kd (nM)", "binding_affinity", "KD" all map to `binding_affinity_kd`) to the normalized fields, log a warning and put the rest of the row into raw_scores, and never hard-fail an entire import because of one unrecognized column. It should hard-fail (with a clear error returned to the caller) on missing required identifiers (no sequence_id, no fasta_sequence) or a completely unparseable file.

## Synthetic sample data (since I don't have a real export yet)

Generate a `sample_data/her2_nanobody_sample.csv` fixture: ~30-50 synthetic candidate rows modeled loosely on the published shape of Bio Discovery / antibody design output (sequence_id, a short synthetic FASTA-like sequence, binding_affinity_kd values spread roughly 0.5-300 nM, humanness_score 0-1, aggregation_propensity as some bounded score, plus 1-2 extra made-up recipe-specific columns to exercise the raw_scores JSONB path). Label this file and any code referencing it clearly as **synthetic placeholder data, not real experimental output** — in the file header, in code comments, and in the README. Use it for local dev, tests, and the demo seed script. Once I run a real Bio Discovery experiment and export a CSV, I'll drop the real file in and we'll adjust the column-mapping table if needed — that should be the only required change.

## REST endpoint (CSV import)

`POST /experiments/{experiment_id}/candidates/import` — multipart file upload, plus optional create-experiment-on-the-fly variant: `POST /experiments` (JSON body: name, recipe_name, target_name, target_pdb_id, notes) returns an experiment id, then import candidates against it. Import response should report rows processed, rows imported, rows skipped with reasons, and any column-mapping warnings.

Also: `GET /health` for future ECS health checks.

## GraphQL API (everything else)

Queries:
- `experiments`: list with pagination, filterable by target/recipe
- `experiment(id)`: single experiment with its candidates
- `candidates`: filterable/sortable across all experiments — by experiment, by binding_affinity_kd threshold, by humanness_score threshold, by aggregation_propensity threshold, sortable by any normalized field
- `topCandidates(experimentId, limit, criteria)`: a comparison/ranking query — e.g. best combined binding affinity + humanness within an experiment
- expose `raw_scores` as a JSON scalar so recipe-specific fields are queryable even though they're not normalized columns

Mutations (no file handling here — that's REST):
- `createExperiment` (or skip if REST handles this — pick one path, don't duplicate)
- `annotateCandidate(id, note)` or similar light metadata mutation, useful for a future "flag this candidate as interesting" UI feature

Enable CORS broadly enough that a future separately-hosted Svelte/React dev server can hit this API without rework.

## Tests

- Unit tests for the CSV importer: happy path against the sample fixture, missing required columns, unrecognized extra columns landing in raw_scores, malformed rows, empty file.
- GraphQL query tests: filtering, sorting, the comparison query.
- At least one integration test that spins up against a real (test) Postgres — docker-compose based test setup or testcontainers, your call.

## Deliverables

- New repo, structured like a typical FastAPI project (app/, tests/, sample_data/, docker-compose.yml, Dockerfile, .env.example, pyproject.toml or requirements).
- README covering: what this is and why (the AWS Summit / Bio Discovery / ChemE-background story, briefly), the no-public-API constraint and why CSV import is the integration point, current data status (synthetic until a real export lands), how to run locally (docker-compose up), how to hit the GraphQL playground, a curl example for the import endpoint, and the data model rationale (normalized fields + JSONB).
- **CLAUDE.md** in the repo root — project memory for Claude Code sessions on any machine. This is a multi-machine project (Mac + PC), so every Claude Code instance starts cold. CLAUDE.md should cover: project purpose and portfolio context, the no-public-API constraint and why (UI-only platform, no boto3/SDK as of June 2026), the two-headed API design decision (REST for CSV upload, GraphQL for queries/mutations) and why GraphQL wasn't used for both, the data model rationale (normalized key metrics + JSONB for recipe-variable columns), current data status (synthetic sample until a real Bio Discovery export is run and dropped into sample_data/), ECS deployment deferred to next sprint, and any non-obvious conventions in the codebase. A new Claude Code session on either machine should be able to read this and contribute without re-litigating settled decisions.
- Working docker-compose setup I can run with one command.
- GitHub Actions CI (lint, type-check, test, build — no deploy yet).

## Explicitly out of scope this sprint

- Any frontend (Svelte/React) — just don't block it architecturally.
- ECS deployment — Dockerize cleanly, but don't write deploy infrastructure yet.
- Any direct integration with Bio Discovery beyond manual CSV export/import — there is no API to integrate with.
- Authentication/authorization — fine to leave fully open for a local portfolio demo; note in the README that this would need addressing before any real deployment.
