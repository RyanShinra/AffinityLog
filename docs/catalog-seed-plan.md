# Chapter 2 — Catalog Seeding (plan & multi-machine handoff)

> Read this cold on the PC to continue. Authored on the Mac at the end of the data-layer chapter.

## Where we are
- **PR #1 is merged to `main`.** `main` has the full data layer: 9 ORM model classes + the `recipe_modules`
  association table (10 tables) on `ModelBase`, async SQLAlchemy 2.0 + asyncpg, Alembic `001_initial_schema`,
  and `tests/test_models.py`. Schema verified in real Postgres.
- This branch **`catalog-seed`** (off `main`) is Chapter 2: load the **catalog** (reference data —
  `modules`, `metrics`, `concepts`, `recipes`) so the importer + GraphQL have something to map to / query.

## What "data" this is (and what it is NOT)
- **Catalog = reference data**, hand-authored, curated from `~/ClaudeCowork/model-reference/MODULE_OUTPUTS.md`
  (a digest of 34 module-repo READMEs). This is what we seed now.
- **Experiment data** (`candidates`/`experiments`/`artifacts`) comes later via the CSV importer + a real
  Bio Discovery run. None exists yet (only synthetic `sample_data/her2_nanobody_sample.csv`).
- **Provenance matters:** almost every column/direction/unit is INFERRED from READMEs. Only `pseudo_perplexity`
  (column) and the SFvCSP Mahalanobis transform are AWS-confirmed. The authoritative source is the ~195
  Bio Discovery "Module Evaluation" benchmark pages — **NOT scraped (ToS); revisited manually.** That's why
  metrics get a `provenance` column: so a future "Ryan feels like clicking 195 buttons" day knows what's left.

## Design decisions (settled — don't re-litigate)
- **Format: JSON** (`seed/catalog.json`), not YAML. Stdlib (zero new dep), robust (no YAML indentation /
  "Norway" `no→false` traps), trivial to machine-generate if we automate curation later. Human-facing notes
  live as data fields (`notes`, `provenance`), not comments.
- **Loader: async** (`scripts/seed_catalog.py`) — reuse `AsyncSessionLocal` under `asyncio.run(...)`.
- **Idempotent via Postgres `INSERT ... ON CONFLICT ... DO UPDATE`** on natural keys:
  `concepts.name`, `modules.name`, and `metrics` → constraint `uq_metric_identity`
  (`module_id, column_key, variant_kind, variant`). Re-runnable: edit JSON, re-run, rows UPDATE not duplicate.
  This is where `NULLS NOT DISTINCT` pays off — a re-seed of a NULL-variant metric updates the same row
  instead of inserting a dup.
- **FK resolution: two-pass name→id (build the graph bottom-up).** Upsert `concepts` → `{name: id}` map;
  upsert `modules` → `{name: id}` map; then upsert `metrics`, resolving `concept_id`/`module_id` from the maps.
  Same dependency order as the migration's `create_table`s.
- **Provenance lives in the DB** (new column on `metrics`).

## TODO (ordered — do them top to bottom)
1. **FIRST: add `provenance` to `metrics`.**
   - New enum in `orm.py`: `class Provenance(enum.Enum): INFERRED = "inferred"; AWS_CONFIRMED = "aws_confirmed"`.
   - Column: `provenance: Mapped[Provenance] = mapped_column(SAEnum(Provenance), default=Provenance.INFERRED, server_default=text("'inferred'"))`.
   - New migration `002_add_metric_provenance` via `alembic revision --autogenerate`. **Review it**: it creates
     the new ENUM type — make sure the downgrade `DROP TYPE provenance` (autogenerate omits enum-type drops; see
     how `001`'s downgrade handles it).
   - Re-apply: `alembic upgrade head`. Extend `tests/test_models.py` if useful.
2. **Author `seed/catalog.json`** — the slice first (below).
3. **Write `scripts/seed_catalog.py`** — async, `ON CONFLICT` upsert, two-pass FK resolution, sets `provenance`.
4. **Validate**: run the loader TWICE → row counts stable (idempotency proof); query seeded rows; spot-check a
   variant, a concept, the transform, and provenance values.
5. **Expand** `catalog.json` to the full ~42 modules.
6. **(later)** CSV importer + a real Bio Discovery run → validates the importer AND corrects inferred columns.

## Slice to start (exercises every schema feature)
- `Boltz2.plddt` (concept `structure_confidence`, unit `frac_0_1`, HIGHER) + `ColabFold.plddt` (same concept,
  unit `pct_0_100`) — cross-module concept with different units.
- `PLM-Perplexity.pseudo_perplexity` — `PARAMETER` variant `esm`/`amplify` (provenance AWS_CONFIRMED), LOWER.
- `igdesign.scRMSD` — `SOURCE_MODEL` variant `ABB2`/`ABB3`/`ESMFold`, unit `angstrom`, LOWER.
- `FastDPE.SFvCSP` — `transform = {"type": "mahalanobis_gaussian", "params": {"mu": 3.59, "sigma": 7.47}}`
  (provenance AWS_CONFIRMED).

## PC first-run
```bash
git fetch && git checkout catalog-seed
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # if no env yet
docker compose up -d db --wait
.venv/bin/alembic upgrade head        # re-run after writing migration 002
# then start TODO #1
```
The `model-reference/` repos are NOT needed to run the loader — only to author new catalog rows.

## Pointers
- Schema: `app/models/orm.py`. Migration: `migrations/versions/001_initial_schema.py`.
- Recon digest (Mac-local): `~/ClaudeCowork/model-reference/MODULE_OUTPUTS.md`, `MODULE_COVERAGE.md`.
- Loader stub to replace: `scripts/seed_sample_data.py` is a cleared placeholder (the old name); new loader is
  `scripts/seed_catalog.py`.
