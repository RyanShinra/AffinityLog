# AffinityLog

A backend for antibody-design experiment data — built around one problem: **the same score column
means different things depending on what was fed into it.**

The data comes from [Amazon Bio Discovery](https://aws.amazon.com/bio-discovery/), AWS's
computational antibody engineering platform, demoed at the June 2026 AWS Summit. Over a free trial I
ran 11 real experiments against **HER2** (the target of trastuzumab/Herceptin), exported the results,
and built this to store and interpret them.

It is a portfolio project — FastAPI, async SQLAlchemy 2.0, PostgreSQL 16, Alembic, Docker — but the
interesting part is not the stack. It is what the data turned out to require.

---

## The problem this exists to solve

Bio Discovery pipelines ("recipes") chain together modules — a design model, a folding model, a
humanness scorer — and every module emits its own columns. Different recipes emit different columns,
so a fixed schema is wrong on arrival. That much is a normal JSONB-bag problem.

Here is the part that is not normal. This corpus contains one column, `boltz2.protein_iptm`, that
measures **three different physical quantities**:

| what was folded | n | what ipTM actually measures | observed |
|---|---|---|---|
| antibody + HER2 | 4 | genuine binding to the target | 0.196 – 0.793 |
| antibody alone (heavy + light) | 3 | the antibody's own two chains pairing | 0.948 – 0.962 |
| a single chain | 2 | nothing — there is no interface | 0.000 |

Same module, same column, same run. The three bands **do not overlap**, and the meaningless ones
score *highest* — so sorting candidates by "binding score" puts every structure that contains no
target above every structure that does.

Nothing in the column name, the value, or the module says which case you are looking at. The
discriminator lives in a **different table**: which chains the candidate has. That single fact shapes
most of the design below.

---

## What the data actually is (60 seconds, no biology needed)

An **antibody** is a protein whose entire job is to stick to one specific target and nothing else.
The target here is **HER2**, a receptor that is overproduced in roughly one in five breast cancers —
and the target of the real drug trastuzumab (Herceptin).

Designing one is a search problem, and Bio Discovery runs it as a pipeline of ML models. Each model
answers one question a drug program has to ask, and each writes its answers as columns in the CSV
export. That is what this database stores:

| the question | modules | what they emit |
|---|---|---|
| **Does it stick to the target?** | Boltz2 | Predicts the 3D structure of antibody and target *together*, then scores its own confidence: **ipTM** (0–1, "is this interface real?") and **pLDDT** (0–1, "is this shape right?"). Neither is a measured binding strength — they are the model's confidence in its own picture. |
| **Where does it grab?** | structure analysis | Reads that predicted structure geometrically: which target residues are in contact (the **epitope**), how many, how close. This is what the demo paints red. |
| **Will the patient's immune system attack the drug itself?** | BioPhi, Humatch | An antibody that doesn't look human can provoke an immune response against the drug. **Humanness** scores how closely the sequence resembles real human antibodies — BioPhi's *OASis* is a percentile against a database of observed human antibody sequences; Humatch is a neural net trained on the same question. Running both is deliberate: they can disagree. |
| **Can we actually manufacture and store it?** | liabilities, TemStaPro | Certain amino-acid motifs oxidise, degrade, or pick up sugar molecules in the wrong place over time. These count them. TemStaPro predicts heat tolerance. A brilliant binder that falls apart in a vial is not a drug. |
| **Is this sequence even plausible?** | ESM2, EvoProtGrad | Protein language models — trained on known protein sequences much as an LLM is trained on text. **Pseudo-perplexity** is literally "how surprised is the model by this sequence". EvoProtGrad uses one to mutate a starting antibody toward better scores. |
| **Design one from scratch** | RFantibody | Generates a new binder against a target rather than improving an existing one. Much harder, and the scores show it. |

Two things worth knowing before reading any number in this repo:

1. **Everything here is predicted.** No wet-lab assay was run. These are model outputs, and a
   confident model can be confidently wrong.
2. **The same metric name means different things across models.** pLDDT is 0–1 in Boltz2 but 0–100 in
   ColabFold; "ipTM" from a folding model and from a binder-design model are used differently. That
   is why the catalog keys a metric by its *producing module*, never by column name alone — and why
   the ipTM problem above is a symptom of a general disease, not a one-off.

---

## What is actually here

**Working today**

- A **PostgreSQL schema** holding 9 experiments, 14 candidates, 26 chains, 11 predicted structures.
- A **catalog** of 144 metrics across 16 modules, giving each raw score key a display name, unit,
  direction, value type, and — where it matters — a warning about how it misleads.
- A **`/demo` page**: the corpus rendered live from the database, with 3D structures (3Dmol.js,
  vendored so it needs no network), chains coloured by role, and the predicted epitope painted onto
  HER2.
- **Idempotent loaders** for the corpus, the catalog, and the derived recipes.

**Not built yet — and the README will say so until it is**

- **GraphQL** (`/graphql`). `app/graphql/{types,schema}.py` are 115-byte placeholders. The schema was
  designed first (`docs/graphql-schema-handoff.md`) and the catalog was built to feed it; the
  resolvers are the next chapter.
- **REST ingestion** (`POST /experiments/…/import`). Also placeheld. CSVs are loaded today with
  `scripts/load_experiment.py`, a deliberate stand-in.

The live HTTP surface is exactly: `GET /health`, `GET /demo`, `GET /demo/pdb/{candidate_id}`, and
`/static`. Nothing else responds.

---

## The data model

Three layers, each solving a different problem.

### 1. The bag — absorbing whatever a recipe emits

`candidates.scores` is JSONB (GIN-indexed), keyed by the full export header:

```json
{ "boltz2.protein_iptm": "0.793", "biophi.OASis Percentile_After.H": "0.24", ... }
```

Values are stored as **text, uncoerced**. 200 distinct keys across the corpus; a new recipe with new
columns needs no migration.

### 2. The normalized parts — what the bag cannot answer

`candidate_chains` holds 0..N chains per candidate (`HEAVY`, `LIGHT`, `TARGET`) with their sequences.
This is what makes the ipTM problem solvable: chain composition is queryable, so a view can classify
each row.

It also enables an antibody **fingerprint** — an md5 of the heavy+light sequences, target excluded —
so the *same molecule* is recognisable across experiments even when it was folded with a target in
one and without in another. That is how the demo traces one antibody through a design→humanization
fork that spans two separate CSV exports.

### 3. The catalog — what a score *means*

`modules` → `metrics` → `concepts`. A metric's identity is
`(module, column_key, variant_kind, variant)`, which is more than a column name because column names
collide. `VariantKind` distinguishes the axes along which one name can mean several things:

- `PARAMETER` — EvoProtGrad emits `esm_pseudolikelihood_ratio` and `amplify_pseudolikelihood_ratio`:
  one metric, two language models.
- `INTERFACE` — the ipTM case above. `boltz2.protein_iptm` is stored as **three rows**, one per
  interface kind, each with its own display name and direction. The constant-zero variant is marked
  `NEUTRAL` so nothing can sort by it.

Metrics carry `provenance` (`INFERRED` from a module README vs `AWS_CONFIRMED`) and `notes` — the
caveat that has to travel with the number:

> *"~0.95 observed. NOT binding: no antigen was in the fold. Runs higher than any genuine complex,
> so it tops a naive ipTM leaderboard."*

Of the 138 metric identities in the corpus, 22 are hand-curated (as 28 rows); the other 116 are
registered as **uncurated** — display name = the raw key, direction `NEUTRAL`, value type inferred
from the actual data. An uncurated metric looks uncurated rather than being absent, so a consumer
always finds a row and never has to special-case a missing one.

### The view

`candidate_summary` flattens the bag into named columns and adds the derived `interface_kind`.
It is created by migration `004`, and `scripts/check_view_migration.py` runs in CI to fail the build
if `sql/candidate_summary.sql` and the migration drift apart.

---

## Running it

```bash
cp .env.example .env
docker compose up --build       # Postgres + app; runs `alembic upgrade head`
```

That gives you a schema at head and an **empty database** — the corpus is not fixture data, so it is
loaded explicitly:

```bash
# 1. the experiment CSVs (one per export)
python scripts/load_experiment.py "experiment_results/experiment_1/<results>.csv" --name "HER2 Round 1"

# 2. the catalog: curated meanings, then every remaining key as uncurated
python scripts/seed_catalog.py
python scripts/seed_metric_skeleton.py

# 3. campaign context (project, target, structure files) and derived recipes
python scripts/seed_corpus_context.py
python scripts/seed_recipes.py
```

Every loader is idempotent — re-run them freely; they converge rather than duplicate.

Then open **http://localhost:8000/demo**.

> Structures are read from `experiment_results/` on disk. Running the app inside Docker needs that
> directory bind-mounted, or `/demo/pdb/{id}` will 404 — see the open items below.

### Tests

```bash
pip install -e ".[dev]"
pytest -v
```

Postgres comes from [testcontainers](https://testcontainers.com/) automatically; set
`TEST_DATABASE_URL` to point at an existing instance instead.

---

## Repository map

| path | what |
|---|---|
| `app/models/orm.py` | the schema, heavily commented with the reasoning behind each decision |
| `app/models/views.py` | read-only model over `candidate_summary` (deliberately excluded from Alembic autogenerate) |
| `app/routers/demo.py` | the demo page and the path-validated structure endpoint |
| `sql/candidate_summary.sql` | the view, annotated — the iterate-in-a-GUI copy |
| `migrations/versions/` | 001–006 |
| `seed/catalog.json` | the curated meaning layer |
| `scripts/` | loaders, the score-key extractor, and the HTML provenance scrapers |
| `docs/schema-stress-log.md` | **the most interesting file in the repo** — a running log of what each experiment taught us about the schema, including the ipTM finding |
| `docs/demo-biology.md` | the biology, written for a software engineer |

---

## Known gaps

Kept here rather than in a private list, because a portfolio repo should be honest about its edges:

- **GraphQL and REST ingestion are not implemented** (see above).
- **Recipe topology is lossy.** `recipe_modules` records *which* modules a recipe used, not how they
  were wired. The real DAG edges are recoverable from archived HTML (`scrape_recipe_dag`) but need a
  schema decision first — `docs/recipe-topology-note.md`.
- **`experiment_results/` is not bind-mounted** in `docker-compose.yml`, so structures 404 in Docker.
- **`targets` / `projects` / `recipes` have no unique constraint on `name`**, so their loaders use
  look-then-insert instead of a real upsert.
- **Benchmark tables are empty.** AWS publishes per-metric validation statistics across ~195 pages;
  collecting them was deferred on terms-of-use grounds.
- **Everything here is predicted, not measured.** ipTM, pLDDT and humanness are model outputs, not
  assay results. Folding in published reference data to get measured ground truth is the stretch goal
  in `docs/published-data-goal.md`.

## How this was built, and who built what

Worth stating plainly, because it is half the point of the exercise.

I am a senior backend engineer — TypeScript and Node primarily, Python developing — and this project
runs two goals at once: learn Python data-layer design properly (SQLAlchemy 2.0 async, Alembic, and
the Postgres features I had read about but never actually reached for), and work out what it looks
like to use an AI engineer as a genuine collaborator on a domain I am new to, rather than as a
fancier autocomplete.

The division of labour:

- **I own the decisions.** Scope, schema shape, what gets built next, what gets deferred, what a
  column should be called, and when a mechanically-derived answer is good enough versus when it has
  to be right. Also the domain judgment — I ran the experiments and know what the platform was
  actually doing.
- **Claude does most of the typing, and knows this design space better than I do.** Postgres's
  `UNIQUE NULLS NOT DISTINCT` and why it makes an upsert idempotent, that a view's ORM model has to
  stay out of Alembic's metadata, the asyncpg parameter-binding quirks — those came from the AI, and
  I understand them now because they were explained rather than just inserted.
- **The load-bearing bits I wrote myself, on purpose.** The `CASE` expression that classifies
  interface kind, the sequential-revision hook in `migrations/env.py`, the enum migration in `005`,
  the CSV score-parsing in the importer. Typing the piece that encodes the actual decision is how the
  learning happens; having it reviewed afterwards is how the bugs get caught.
- **It goes both ways.** Claude has been confidently wrong here in ways I caught — most memorably an
  Alembic `autocommit_block()` recommendation that cannot work in this codebase at all. A fair
  number of the comments in this repo exist because a wrong answer turned out to be worth recording
  next to the right one.

That collaboration is also where the findings came from. The ipTM discovery in
`docs/schema-stress-log.md` surfaced because widening the demo from three structures to nine raised a
question neither of us had thought to ask — and then took a deliberately slow afternoon of checking
against the actual data instead of accepting the first plausible explanation. The commit history is
the honest record of that, wrong turns included.

## A note on the data

The experiments are real, but small: single diffusion samples, a handful of candidates, run on a free
trial to see what the platform did. The corpus is a well-documented anecdote, not a study. Its value
here is that it was messy enough to break naive assumptions — which is exactly what a schema needs to
be tested against.

## Security

No authentication or authorization — this is a local demo. CORS is open. Before any deployment, add
authn and scope CORS to specific origins.
