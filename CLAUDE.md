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

Three layers. See `README.md` for the full explanation and `app/models/orm.py` for the commented
schema.

1. **`candidates.scores` (JSONB, GIN-indexed)** — the bag. Keyed by the full export header
   (`boltz2.protein_iptm`), values stored as **text, uncoerced**. 200 distinct keys in the corpus;
   a new recipe needs no migration.
2. **`candidate_chains`** — 0..N chains per candidate (HEAVY/LIGHT/TARGET) with sequences. Not a
   nicety: chain composition is what disambiguates the bag (below), and hashing heavy+light gives an
   antibody fingerprint that tracks one molecule across experiments.
3. **The catalog** (`modules` → `metrics` → `concepts`) — what a score MEANS. Metric identity is
   `(module, column_key, variant_kind, variant)`, because column names collide.

**The finding that drove the design (2026-07-31):** `boltz2.protein_iptm` measures three different
physical quantities depending on which chains were folded — real HER2 binding (0.196–0.793), the
antibody's own heavy–light pairing (~0.95), or nothing at all (0.000 for a lone chain). The
meaningless values score HIGHEST, so a naive sort inverts the ranking. The discriminator is not in
the key or the value; it is derived from `candidate_chains` by a CASE in the `candidate_summary`
view, and stored in the catalog as `VariantKind.INTERFACE`. Full write-up in
`docs/schema-stress-log.md`.

**Do not reintroduce** `binding_affinity_kd` / `humanness_score` / `aggregation_propensity` /
`raw_scores` — those were the pre-003 schema and were removed deliberately.

---

## Current data status

**REAL DATA.** 11 Bio Discovery experiments were run against HER2 during the free trial
(2026-06 to 2026-07); 9 exported successfully and are loaded: 9 experiments, 14 candidates,
26 chains, 11 predicted structures, 144 catalogued metrics. Raw CSVs and structures live in
`experiment_results/`, archived HTML in `HTML Extracts/`.

Loading is by script, not by API — `scripts/load_experiment.py` for CSVs, then
`seed_catalog.py` → `seed_metric_skeleton.py` → `seed_corpus_context.py` → `seed_recipes.py`.
All idempotent. `sample_data/her2_nanobody_sample.csv` is the old synthetic fixture and is no
longer representative of the schema.

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

- `candidates.scores` stores every CSV column as `dict[str, str]` (raw strings, not coerced).
  `scripts/seed_metric_skeleton.py` infers each metric's `value_type` from those strings.
- Migrations run 001–006. `alembic upgrade head` before starting the server (docker-compose does it).
  **Use `alembic revision -m "..."` to scaffold** — `env.py` has a hook that numbers revisions
  sequentially, and `revision_environment = true` makes it fire for plain revisions too. Do not
  hand-write migration files.
- **`app/models/views.py` is deliberately NOT imported by `migrations/env.py`** — it maps the
  `candidate_summary` VIEW, and importing it would make autogenerate emit CREATE/DROP TABLE for it.
- The view is created by migration 004 and duplicated in `sql/candidate_summary.sql` (the
  iterate-in-TablePlus copy). `scripts/check_view_migration.py` runs in CI and fails the build if
  the two define different columns.
- **Tombstoned (115-byte placeholders, not yet rebuilt):** `app/graphql/{types,schema}.py`,
  `app/routers/experiments.py`, `app/importer/column_mapping.py`, `app/schemas/pydantic.py`.
  The live HTTP surface is only `/health`, `/demo`, `/demo/pdb/{id}`, `/static`.
- The GraphQL context is designed to pass the SQLAlchemy engine (not a session) so each resolver
  opens its own — avoids session-lifetime issues with async resolvers. Not yet implemented.
- asyncpg quirks that cost time: array params need a real Python list (not `'{A,B}'`), and one named
  parameter cannot be reused across an INSERT target column and a comparison.
- Tests use testcontainers (pulls `postgres:16-alpine` at runtime) or `TEST_DATABASE_URL`
  env var if you want to point at an existing Postgres.

---

## Dev environment (multi-machine)

- **PC:** WSL Ubuntu is the default shell — VS Code opens the repo via the `/mnt/f/...` mount
  (same working tree, not a separate clone). Assume WSL bash unless the user says otherwise;
  avoid defaulting to PowerShell or Git Bash for Python/Alembic/Docker work.
- **Mac:** native terminal.
- **`.venv` is OS-specific and gitignored** — build it fresh per machine/shell
  (`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"` on WSL/Mac;
  `python -m venv .venv && .venv\Scripts\pip install -e ".[dev]"` if ever run natively on Windows).
  Since PC WSL and PC-native share one `.venv` folder path on disk, rebuilding from WSL
  overwrites a Windows-built one (and vice versa) — expected, not a bug.
- **Docker Desktop's WSL2 backend is shared** — containers started from PowerShell are visible
  and usable from WSL bash (`docker compose ps`) without restarting, as long as WSL integration
  is enabled for the Ubuntu distro (Docker Desktop → Settings → Resources → WSL Integration).
- **Lint/format autofix runs two ways, both need one-time setup per machine:**
  - *Pre-commit hook* (`.pre-commit-config.yaml`, ruff --fix + black) — git hooks live in
    `.git/hooks/`, which isn't tracked, so run `.venv/bin/pre-commit install` once per
    clone/machine after building `.venv`. From then on every `git commit` autofixes and
    reformats staged files before the commit lands.
  - *VS Code format-on-save* (`.vscode/settings.json`) — needs the `ms-python.black-formatter`
    and `charliermarsh.ruff` extensions (listed in `.vscode/extensions.json`, VS Code will
    prompt to install them) on whichever machine's VS Code you're using; this is separate from
    the pre-commit hook and doesn't carry over between Mac/PC automatically.

---

## Quick start

```bash
cp .env.example .env
docker compose up --build
# GraphQL playground: http://localhost:8000/graphql
# REST docs:         http://localhost:8000/docs
# Health:            http://localhost:8000/health
```
