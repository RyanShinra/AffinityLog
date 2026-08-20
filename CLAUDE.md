# AffinityLog — Claude Code Project Memory

Read this before contributing in any session. This is a multi-machine project (Mac + PC), so
**assume no memory of previous sessions carries over** — everything needed lives in the repo.

Starting cold, read in this order:

1. **this file** — conventions, how the owner works, what not to reintroduce
2. **`README.md`** — what the project is and the problem it solves; current by construction
3. **`docs/README.md`** — an index of the other docs, marking which are current and which are
   historical handoffs that will contradict the README (the README wins)

Then verify anything load-critical against the running database rather than against a document.

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
(2026-06 to 2026-07) and 9 exported successfully, but **7 CSVs are what load** — they produce
9 experiment rows, because `load_csv` creates one Experiment per distinct `experimentId` and
experiments 1 and 5 each span two sub-experiments. The loaded corpus is 9 experiments,
14 candidates, 26 chains, 200 distinct score keys, 11 predicted structures, 144 catalogued
metrics. Raw CSVs and structures live in `experiment_results/`, archived HTML in
`HTML Extracts/`.

Experiments 9 and 10 (the ESM2 pair) exported fine but are deliberately **not** loaded: the
ESM2 Only recipe folds nothing, so they have no predicted structures to pair with. Loading
them would take the key count to 204. This is a "not yet" — see the docstring of
`experiment_results/load_experiment_results.py`, which is the authoritative record of which
files load and under what names.

Loading is by script, not by API. To rebuild from empty:
`experiment_results/load_experiment_results.py` (all 7 CSVs, one transaction), then
`seed_catalog.py` → `seed_metric_skeleton.py` → `seed_corpus_context.py` → `seed_recipes.py`.
The seeders are idempotent; the corpus loader skips CSVs already loaded by `source_filename`.
`scripts/load_experiment.py` is the general single-CSV tool that the corpus loader wraps — use
it for a new export, not for rebuilding this corpus.
`sample_data/her2_nanobody_sample.csv` is the old synthetic fixture and is no longer
representative of the schema.

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

## Working with the owner (read this before writing code)

The owner is a senior backend engineer — TypeScript/Node primary, Python developing. This project is
partly a vehicle for learning Python data-layer design, so *how* work happens matters as much as what
ships. These were learned the hard way; they are not preferences to optimise away.

- **Discuss the approach before building it.** For anything past a trivial edit, propose the plan,
  name the decision points, and wait. Building first pre-empts the owner's input and wastes his time.
- **Leave the decision-carrying code to him.** He writes the pieces that encode a judgment — the
  `CASE` classifying interface kind, the `env.py` revision hook, migration `005`, the CSV score
  parsing. Scaffold around it, mark the spot, explain the trade-offs, and offer a "fill in the
  blanks" version rather than assuming he wants to type boilerplate.
- **Never start, stop, or restart Docker — ask, or hand the command over.** Launching Docker
  Desktop spins up a VM, mounts filesystems, and starts any container with a restart policy; on a
  laptop that is a real battery and memory commitment, and it changes machine state well beyond the
  task at hand. If a task needs the daemon, say so and give him the command. The same goes for
  `docker compose down -v` and anything else that would destroy the `pg_data` volume.
- **Consult the linter before saying "run it."** Editor diagnostics have caught errors that were then
  shipped anyway; treat ruff/mypy/black as a pre-run gate, and reason about the installed library's
  real signatures rather than the remembered ones.
- **Move deliberately; verify against the data.** One checked query beats three plausible paragraphs.
  Nearly every finding in `docs/schema-stress-log.md` came from stopping to measure something instead
  of asserting it — including the ipTM discovery, which contradicted the obvious hypothesis. When he
  asks a probing question ("what's the bonehead case here?"), that is a genuine request to find the
  failure mode, not doubt to be reassured away.
- **SQL background:** fluent at reading and writing queries, but new to authoring `.sql` files and
  DDL. Explain file structure, statement shape, and *where a clause goes* — not query semantics,
  which he already knows. `CASE` reads as a chained ternary; that framing lands.

## Non-obvious conventions

- `candidates.scores` stores every CSV column as `dict[str, str]` (raw strings, not coerced).
  `scripts/seed_metric_skeleton.py` infers each metric's `value_type` from those strings.
- Migrations run 001–007. `alembic upgrade head` before starting the server (docker-compose does it).
  **Use `alembic revision -m "..."` to scaffold** — `env.py` has a hook that numbers revisions
  sequentially, and `revision_environment = true` makes it fire for plain revisions too. Do not
  hand-write migration files.
- **`app/models/views.py` is deliberately NOT imported by `migrations/env.py`** — it maps the
  `candidate_summary` VIEW, and importing it would make autogenerate emit CREATE/DROP TABLE for it.
- The view is created by migration 004 and duplicated in `sql/candidate_summary.sql` (the
  iterate-in-TablePlus copy). `scripts/check_view_migration.py` runs in CI and fails the build if
  the two define different columns.
- **Tombstoned (115-byte placeholders, not yet rebuilt):** `app/routers/experiments.py`,
  `app/importer/column_mapping.py`, `app/schemas/pydantic.py`. (`tests/conftest.py` was on this
  list until the database fixtures were rebuilt — verify with `wc -c` before believing any entry.)
  The live HTTP surface is `/health`, `/demo`, `/demo/pdb/{id}`, `/graphql`, `/static`.
- **The GraphQL context passes one `AsyncSession` per HTTP request, shared by every resolver in the
  query tree** — not an engine, and not a session per resolver. A GraphQL query is a tree, so one
  request touches experiments, their candidates and those candidates' chains; separate sessions would
  spread one logical read across several transactions. It also has to outlive the entry-point
  resolver, because type resolvers run after that has already returned. `app/database.py`'s
  `get_session` provides the shape; `app/graphql/context.py` wires it in.
  (This entry previously said the opposite — engine-per-resolver — which the code never did.)
- asyncpg quirks that cost time: array params need a real Python list (not `'{A,B}'`), and one named
  parameter cannot be reused across an INSERT target column and a comparison.
- **Tests provision their own Postgres via testcontainers** — `postgres:16-alpine`, the same image
  `docker-compose.yml` pins, started once per pytest session and thrown away after. Docker must be
  running; nothing else needs setting up, on either machine or in CI.
  There is deliberately **no `TEST_DATABASE_URL` escape hatch**. It was considered and rejected: the
  obvious thing to point it at is the dev database on `localhost:5432`, and the fixtures assert
  absolute counts against an empty schema, so that would fail confusingly (144 metrics, not 6) while
  also aiming `alembic upgrade head` at real data. A second database inside the compose container
  was rejected for a different reason — CI has no compose stack, so it would reintroduce a
  local-vs-CI split, which is the thing testcontainers exists to remove.

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
