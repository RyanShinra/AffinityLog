# AffinityLog

A backend for antibody-design experiment data — built around one problem: **the same score column
means different things depending on what was fed into it.**

The data comes from [Amazon Bio Discovery](https://aws.amazon.com/bio-discovery/), AWS's
computational antibody engineering platform, demoed at the June 2026 AWS Summit. Eleven real
experiments were run against **HER2** (the target of trastuzumab/Herceptin) over a free trial; this
repository stores and interprets their exported results.

It is a portfolio project — FastAPI, async SQLAlchemy 2.0, PostgreSQL 16, Alembic, Docker — but the
stack is not the interesting part. What the data turned out to require is.

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

<details>
<summary><b>"JSONB bag, GIN-indexed" — what that actually means</b> (worth expanding if you haven't used Postgres this way)</summary>

**JSONB** is a Postgres column type that stores a whole JSON document per row, parsed into a binary
form rather than kept as text. So one `candidates` row holds one `scores` document containing however
many key/value pairs that experiment happened to produce. "Bag" is just an informal name for the fact
that it is an *unstructured* pile of pairs — no fixed set of keys, no schema, and two rows can hold
completely different ones. That is the whole reason it's here: recipes emit different columns, so
there is no correct fixed column list to migrate to.

**GIN** stands for *Generalized Inverted Index*, and the important word is **inverted**.

A normal B-tree index — the default, and what the `id` and `(experiment_id, sequence_id)` indexes
above are — maps *one value per row* to that row. That works because a row has exactly one `id`. But
a JSONB document holds *many* keys, so a B-tree over the `scores` column could only answer "find the
row whose entire document equals this exact document," which is useless.

An inverted index flips the direction. Instead of `row → its contents`, it stores
`content → the rows containing it`, exactly like the index at the back of a book maps a word to the
pages it appears on. Postgres walks every key (and value) inside every document and records which
rows contain each one. In this corpus that's 200 distinct keys across 1,132 total key occurrences,
all pointing back at 14 rows.

That makes containment and existence questions fast — the ones written with JSONB's own operators:

```sql
SELECT * FROM candidates WHERE scores ? 'boltz2.protein_iptm';        -- has this key?
SELECT * FROM candidates WHERE scores @> '{"temstapro.thermophilicity.H": "mesophilic"}';
```

**The catch worth knowing:** a GIN index does *not* help when you pull a value out and compare it,
which is what the view does everywhere:

```sql
WHERE (scores->>'boltz2.protein_iptm')::numeric > 0.5     -- GIN index not used
```

`->>` extracts, casts, then compares — that's a computed expression, not a containment test, so
Postgres scans. Making *that* fast needs a separate expression index on that specific key. At 14 rows
it is entirely academic here; on a real corpus it is the difference between the index you have and
the index you need. The trade-offs to weigh: GIN indexes are larger than B-trees and slower to
update, since a single insert touches one entry per key in the document.

</details>

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

### What the demo shows

Three panels, top to bottom. **A 3D viewer** of every predicted structure in the corpus, grouped by
what was actually folded — antibody heavy chain in blue, light chain in green, HER2 target in grey,
and the residues the model predicts are in contact painted red. **A table of every candidate**, where
the empty cells are the interesting part: they show which metrics a given recipe did and did not
emit. **A provenance panel** that follows one antibody across two separate experiments, using a
sequence fingerprint to prove they are the same molecule even though one run scored its humanness and
the other folded its structure.

The tab groups are the ipTM problem made visible: the ~0.95 scores in the middle group are *not*
better binders, they are folds with no target in them. Hover any column header for what the metric
means and how it misleads.

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
| `docs/schema-stress-log.md` | **the most interesting file in the repo** — a running log of what each experiment revealed about the schema, including the ipTM finding |
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

Stated plainly, because it is half the point of the exercise.

Ryan O. is a senior backend engineer working primarily in TypeScript and Node, with Python
developing. This project runs two goals at once: learning Python data-layer design properly
(SQLAlchemy 2.0 async, Alembic, and the Postgres features that are easy to read about and harder to
reach for), and finding out what it looks like to use an AI engineer as a collaborator on an
unfamiliar domain rather than as a fancier autocomplete.

The division of labour:

- **The decisions are the author's.** Scope, schema shape, what gets built next, what gets deferred,
  what a column should be called, and when a mechanically-derived answer is good enough versus when
  it has to be right. Domain judgment too — he ran the experiments and knows what the platform was
  actually doing.
- **Claude did most of the typing, and knows this design space better.** Postgres's
  `UNIQUE NULLS NOT DISTINCT` and why it makes an upsert idempotent, that a view's ORM model has to
  stay out of Alembic's metadata, the asyncpg parameter-binding quirks — all of that came from the
  model, and stuck because it was explained rather than merely inserted.
- **The pieces that encode a decision were written by hand, on purpose.** The `CASE` expression that
  classifies interface kind, the sequential-revision hook in `migrations/env.py`, the enum migration
  in `005`, the CSV score-parsing in the importer. Typing the part that carries the judgment is where
  the learning happens; review afterwards is where the bugs get caught.
### What two heads actually bought

The useful part was not one party checking the other's work. It was that the two fail in different
directions, so between them they cover ground neither covers alone. Four kinds of error showed up,
and each needed a different catcher.

**Confidently wrong about *this* environment.** The model's failures were rarely nonsense; they were
generically-true advice that happens to be false here, which is the hardest kind to spot by reading.
Alembic's `autocommit_block()` is the textbook way to add a Postgres enum value — and it cannot work
in this codebase, because `env.py` opens the transaction through SQLAlchemy, so Alembic never owns
one to suspend. It fails on an assertion before running any SQL. Also: a `notes` column confidently
referenced on a table that did not have one; `ON CONFLICT (name)` written against three tables with
no unique constraint on `name`; a Postgres array passed as `'{A,B}'` to a driver that wanted a list.
Every one of those was caught the same way — by running it, not by reviewing it.

**Right about things the author had not met yet.** In the other direction: `UNIQUE NULLS NOT DISTINCT`
and why it is the difference between an idempotent seeder and one that silently multiplies rows on
every run; that a view's ORM model must stay out of Alembic's metadata or autogenerate will try to
`DROP TABLE` it; that `alembic revision` skips `env.py` entirely unless `revision_environment` is set.

**Known only to the human.** Some facts are not in the code or the data at all. Recipes in Bio
Discovery are *reusable* — configured per run with different molecules — which makes experiments
relate to them many-to-one. That single correction changed the recipe model from one-row-per-
experiment to shared rows, and it could only come from someone who had used the platform.

**Known to neither, until the data was asked.** The ipTM finding is the clearest case. It did not
come from insight; it came from widening the demo from three structures to nine and noticing that
five of them contained no target. Verifying it meant querying every metric across every interface
kind — which then contradicted the obvious hypothesis that the "i" in ipLDDT marked the
interface-dependent ones. It does not; `complex_iplddt` stays meaningful for a lone chain. The rule
had to be measured, not reasoned.

The pattern across all four: **the disagreements were more productive than the agreements.** Several
comments in this repository exist to record a wrong answer beside the right one, because the wrong
one was the more instructive half. The commit history is the honest version, wrong turns included.

### Authorship

**Author: Ryan O.** — decisions, direction, domain expertise, and accountability for what is here.

**Prose and much of the code: Claude** (Anthropic), via Claude Code, reviewed and approved by the
author. Commits carry `Co-Authored-By: Claude` where that applies.

This follows the convention emerging across academic publishing and open-source projects: an AI is
credited as a tool and its use disclosed, but not listed as an author, because authorship carries
responsibility that a model cannot hold. If something here is wrong, that is the author's problem to
answer for — which is also why every factual claim in this README was verified against the running
database or the repository before it was written.

## A note on the data

The experiments are real, but small: single diffusion samples, a handful of candidates, run on a free
trial to see what the platform did. The corpus is a well-documented anecdote, not a study. Its value
here is that it was messy enough to break naive assumptions — which is exactly what a schema needs to
be tested against.

## Security

No authentication or authorization — this is a local demo. CORS is open. Before any deployment, add
authn and scope CORS to specific origins.
