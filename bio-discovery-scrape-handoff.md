# Bio Discovery Module Evaluation — Scrape Findings & Schema Handoff

Compiled from manual review of the Module Evaluation page (`843bffa81.biodiscovery.aws.com`),
Ryan's own authenticated AWS console session. Purpose: brief Claude Code before building the
scraper (Cowork or Claude Code-driven browser automation) and before writing the next migration.

---

## 1. ToS / legal status

- The AWS **Site Terms** (`aws.amazon.com/terms`) grant a "limited license... for personal use...
  not to download (other than page caching)" — but that governs the public marketing site
  (`aws.amazon.com`), not this page.
- The Module Evaluation page lives on an **authenticated subdomain** under Ryan's own logged-in
  AWS account (`843bffa81.biodiscovery.aws.com`). Automated reading of your own account's data via
  a browser you're already logged into isn't "scraping" in the ToS-restriction sense — it's the
  same access a manual copy-paste would have, just automated.
- No anti-bot/anti-scraping clause found in the AWS Customer Agreement or the generative-AI
  disclosure page that would separately restrict this.
- Service Terms §22.6 (Bio Discovery-specific) prohibits extracting **model internals** (weights,
  parameters, training data) — doesn't apply here; this is reading displayed benchmark
  documentation, not the model itself.
- Practical backstop: this repo is git-tracked. If anything about this later turns out to be
  unwelcome, the seeded rows/migration are easy to revert — nothing here is a one-way door.

**Net: proceed.** Low risk, reversible if wrong.

---

## 2. Page anatomy (build the scraper against this)

### List/grid view (195 cards)
Each card has:
- Index badge (`#1`–`#195`)
- **Card title** — a *display* name, not necessarily the real module name (see gotchas below)
- One or more property/concept **pills**, color-coded by category: Hydrophobicity, Aggregation,
  Expression, Polyreactivity, Purity, and Stability (which itself has sub-pills: Thermostability
  (onset), (Tm1), (Tm2), (Tm3))
- Spearman correlation (value + significance stars, e.g. `-0.329***`)
- Optionally AuROC + Positive ratio — **not universal**, only present when the underlying property
  has a binary/threshold framing

### Filter/search bar (page-level, not per-card)
- Free-text module search
- "Filter by property category" — top-level: Aggregation, Expression, Hydrophobicity,
  Polyreactivity, Purity, Stability (Stability expands to the 4 thermostability sub-pills)
- **Table view / Grid view toggle** — worth checking whether Table view surfaces all 195 rows in
  one flat table. If so, that's a single scrape instead of 195 page loads. Grid view alone would
  require a click-through per card to reach the detail panel fields below.

### Detail panel (right side, one card selected at a time)
- Title + primary pill, repeated
- Aggregate stats: Spearman correlation (value, stars, `n`); optionally a "Classification and
  retrieval metrics" block with AuROC, AuPRC, Precision@Top5% (value + null-distribution range,
  e.g. `0.900 (null dist. 0.195-0.7)`), each with its own `n` and `positive_ratio`
- **"Metrics stratified by antibody type"** — not universal, and even when present may not include
  all four formats (VHH, IgG, ScFv, Near-Germline IgG), each with its own `n` and Spearman
  correlation. One instance showed an info tooltip (ⓘ) next to the IgG stratum — meaning unclear,
  may be worth capturing tooltip text if automation can trigger hover/click.
- **"Generate these predictions in Amazon Bio Discovery"** — this is the ground-truth
  discriminator, not the card title. Format:
  > These predictions can be obtained from the **`<MODULE_NAME>`** Amazon Bio Discovery module
  > using `"<param>=<value>"` parameter and evaluating the `"<column>"` column.
  - `<MODULE_NAME>` is a hyperlink (likely to that module's own page — worth following once to see
    if it's a richer source than the README digest already compiled).
  - Example: card titled "ESM2 Perplexity" → module is actually **PLM Pseudo-Perplexity**,
    `model_type=esm`, column `pseudo_perplexity`.
  - Example: card titled "TNP Patches of Surface Hydrophobicity" → module **TNP**, param
    `"hscale": 0=Kyte and Doolittle`, column `PSH`. Note the **nested quotes inside the parameter
    value itself** — parser needs to handle that, not just split on the outer quote pairs.
- **"Transformation"** block — states whether a transform was applied, including the explicit
  negative case: *"Not transformed because of bimodal distribution."* This is a stated decision,
  not an absence of data — don't collapse it to the same NULL as "we haven't scraped this yet."
- **"Module information" / Metadata** — `Version` (e.g. `1.0`) and `License` (varies per module:
  MIT License, BSD 3-Clause License, etc. — not uniform, don't assume one license for all).

### Page-level content (scrape once, not per-card)
- "Learn more about benchmark dataset used" — describes the **DPBD** (Developability Property
  Benchmarking Dataset): 50 seed antibodies, 42 distinct antigens, up to 99 engineered
  variants/seed across 2 experimental batches, 8 mutation strategies (2 axes: pLM-driven vs.
  non-pLM-driven perturbation; substitution vs. indel), expressed in HEK293, evaluated across 6
  developability assays (expression, purity, thermostability, aggregation, polyreactivity,
  hydrophobicity).
- "Download dataset description" button — may link to a PDF/doc with more detail than the on-page
  paragraph; worth fetching once, separately from the per-card scrape.

---

## 3. Scraper gotchas

- **Card titles collide.** The same title string can map to different concept pills across
  different cards (e.g. "TemStaPro stability score at 60C" appeared paired with Thermostability
  (Tm3) on one card and Thermostability (Tm1) on another). Never key on title alone — key on
  `(title, pill)` at minimum, or ideally on the `(module, param, column)` triple from the
  generation note.
- **Card title ≠ module name.** Always resolve the real module identity from the "Generate these
  predictions" note, never from the card title.
- **Metric shape is non-uniform.** Not every card has AuROC/AuPRC/Precision@Top5%/positive_ratio —
  some report Spearman only. Don't assume the classification block exists; parse defensively.
- **Stratification is non-uniform** — not every card has it, and when present may not cover all
  four antibody formats.
- **Significance stars need splitting from the number** at parse time (`-0.329***` →
  `spearman_correlation: -0.329`, `spearman_significance: "***"`), not stored as one string.
- **Quoted parameter values can contain embedded quotes** (the `hscale` example) — don't naively
  split on `"..."`.
- **`(module, param, column)` from the generation note is not always unique — a `-transformed`
  title suffix can mark a genuinely different card with an identical note.** Confirmed live: FastDPE
  publishes both "...Structural Fv Charge Symmetry Parameter" and "...-transformed" as separate
  cards, both resolving to `module=FastDPE, column=SFvCSP` with no param — the note gives no way to
  tell them apart. The first scrape merged raw+transformed pairs for 6 module/column combos into one
  catalog entry each, silently doubling their benchmark rows and losing/misattributing the
  transform note. See §6 for the full writeup. Capture the title's `-transformed` suffix as its own
  field and fold it into the key alongside `(module, param, column)`.

---

## 4. Scraping approach

The session is tied to Ryan's own AWS login on an authenticated subdomain — a headless
`requests`/`BeautifulSoup` script would need to replicate that auth (likely SSO cookies / possibly
short-lived tokens), which is brittle. Better fit is browser automation reusing the **already
logged-in session** — either Cowork driving the real browser, or a Claude Code-driven
Playwright/Chrome-extension flow pointed at the existing session cookies.

Suggested order of investigation:
1. Check whether **Table view** exposes all fields in one flat, paginated table. If so, that's 1–2
   scrapes instead of 195 page loads.
2. If Table view is missing the detail-panel-only fields (generation note, transformation note,
   stratification, version/license), do a hybrid: bulk-scrape list/table view for card-level
   fields, then a second pass clicking into each of the 195 cards only for the fields exclusive to
   the detail panel.

---

## 5. Schema additions (on top of the already-planned `002_add_metric_provenance`)

**Superseded (2026-07-03) — do not implement `Metric.benchmark_stats` as written below.** This was
written before it was confirmed that one `Metric` validates against *several* developability
properties (see `Metric.benchmark_results` in `app/models/orm.py`'s YOUR-TURN comments and
`docs/schema-erd.md`) — a single JSONB blob per metric can't represent that, and the
`stratified`/`strata` field-name drift between here and there was never reconciled. The live design
is the `BenchmarkResult` table (one row per metric×property) further down this section and in
`app/models/orm.py` — read that, not the block immediately below. Left in place only so the
Pydantic validation pattern (`BenchmarkStats.model_validate(raw).model_dump(mode="json")` before
upsert) isn't lost; adapt it to validate one `BenchmarkResult` row's fields instead of the whole
`Metric`.

- ~~`Metric.benchmark_stats: JSONB, nullable`~~ — validate through a Pydantic model before write,
  then store the dump. Don't let the app write arbitrary keys:

  ```python
  from enum import StrEnum

  class AntibodyFormat(StrEnum):
      VHH = "vhh"
      IGG = "igg"
      SCFV = "scfv"
      NEAR_GERMLINE_IGG = "near_germline_igg"

  class StratumStats(BaseModel):
      n: int
      spearman_correlation: float
      spearman_significance: str | None = None
      auroc: float | None = None
      auprc: float | None = None
      precision_top5: float | None = None
      precision_top5_null_dist: tuple[float, float] | None = None
      positive_ratio: float | None = None

  class BenchmarkStats(BaseModel):
      n: int
      spearman_correlation: float
      spearman_significance: str | None = None
      auroc: float | None = None
      auprc: float | None = None
      precision_top5: float | None = None
      precision_top5_null_dist: tuple[float, float] | None = None
      positive_ratio: float | None = None
      stratified: dict[AntibodyFormat, StratumStats] = Field(default_factory=dict)
  ```

  In `seed_catalog.py`: `Metric.benchmark_stats = BenchmarkStats.model_validate(raw).model_dump(mode="json")`
  before the upsert.

- `Metric.benchmark_dataset_id: nullable FK -> benchmark_datasets.id`
- New table `benchmark_datasets`:

  ```python
  class BenchmarkDataset(ModelBase):
      __tablename__ = "benchmark_datasets"
      id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
      name: Mapped[str] = mapped_column(String(64), unique=True)  # "DPBD"
      version: Mapped[str | None] = mapped_column(String(32))
      description: Mapped[str] = mapped_column(Text)
      seed_antibody_count: Mapped[int | None] = mapped_column(Integer)
      antigen_count: Mapped[int | None] = mapped_column(Integer)
      max_variants_per_seed: Mapped[int | None] = mapped_column(Integer)
      mutation_strategy_count: Mapped[int | None] = mapped_column(Integer)
      assay_count: Mapped[int | None] = mapped_column(Integer)
  ```

  One row, seeded once from the page-level DPBD paragraph — not repeated per metric.

- `Module.version: Mapped[str | None] = mapped_column(String(32))`
- `Module.license: Mapped[str | None] = mapped_column(String(64))`
- `Metric.transform` already exists (JSONB, nullable) — formalize the explicit-negative case as
  `{"applied": false, "reason": "bimodal distribution"}`, distinct from `NULL` meaning
  "not yet scraped."

Suggest bundling all of the above into one migration —
`003_benchmark_stats_and_module_metadata.py` — built on top of `002_add_metric_provenance`, rather
than a separate pass later.

---

## 6. Post-scrape finding: the scrape key is missing a "transformed" discriminator, not flaky

PR review of the scrape (`metrics-scrape` → `catalog-seed`, PR #2) turned up 44
`(module, param, column, property)` groups that appear more than once in
`module_evaluation_catalog.json` (88 of 190 rows) — 36 with conflicting `spearman` values, 8 with
byte-identical ones. **Root cause confirmed against the live page, not a scraper race condition as
first suspected (see prior revision of this section — that theory was wrong):**

Ryan traced the first case by hand: the card titled *"FastDPE Structural Fv Charge Symmetry
Parameter-transformed"* has the generation note *"These predictions can be obtained from the
FastDPE Amazon Bio Discovery module by evaluating the `"SFvCSP"` column"* — **identical** to the
note on the plain, untransformed *"FastDPE Structural Fv Charge Symmetry Parameter"* card. Same
module, same (absent) param, same column — but two genuinely different cards on the live page,
distinguished only by the `-transformed` title suffix, which the generation note (the thing §3
told the scraper to trust over the title) doesn't carry at all. The scraper merged them into one
`module_evaluation_catalog.json` entry, doubling up every one of that entry's benchmark rows.

That single mechanism explains **all 44 groups, both kinds** — confirmed by checking every
affected entry:

| module | column | entry-level `transform` | benchmarks | props | exact-dup | mismatch |
|---|---|---|---|---|---|---|
| FastDPE | cdrHydro | `null` | 2 | 1 | 0 | 1 |
| FastDPE | SFvCSP | `null` | 16 | 8 | 8 | 0 |
| FastDPE | cdrLen | `"transformed (benchmark-derived)"` | 16 | 8 | 0 | 8 |
| TNP | CDR3_length | `"transformed (benchmark-derived)"` | 18 | 9 | 0 | 9 |
| TNP | CDR3_compactness | `"transformed (benchmark-derived)"` | 18 | 9 | 0 | 9 |
| TNP | total_CDR_length | `null` | 18 | 9 | 0 | 9 |

1+8+8+9+9+9 = 44 — every duplicate group in the dataset comes from exactly these 6 merged catalog
entries; nothing outside this table is affected. Two things fall out of this:

- **Exact-dup vs. mismatch is explained by whether the transform is rank-preserving.** Spearman is
  invariant under any strictly monotonic transform of a variable — so a raw/transformed pair whose
  transform happens to be monotonic (SFvCSP, apparently) produces byte-identical Spearman values
  and looked like a harmless re-render; a pair whose transform changes rank order (cdrHydro,
  cdrLen, all three TNP columns) produces different Spearman values and looked like a data
  conflict. Same merge bug, two visible symptoms.
- **The entry-level `transform` field can't be trusted even where it's populated.** cdrLen and both
  TNP entries show `"transformed (benchmark-derived)"` — true of the transformed card in the pair,
  but silently applied to the *merged* entry covering both the raw and transformed rows with no way
  to tell which of each duplicated pair it actually describes. cdrHydro and total_CDR_length show
  `null` outright, i.e. the transform note was dropped entirely during the merge.

**This is a Metric-catalog undercount, not (only) a benchmark-stat data-quality flag.** Raw and
`-transformed` are different derived quantities from the same module+column — arguably two
distinct metrics, not one metric with a data-quality wrinkle. Treating this as a `BenchmarkResult`
data-quality flag (as originally proposed here) would paper over a missing `Metric`, not fix it —
withdrawn.

**RESOLVED (2026-07-03).** Ryan pulled the live Table view's "Module output" column (human-readable
card title, e.g. `FastDPE Structural Fv Charge Symmetry Parameter-transformed`) via manual
copy-paste — a separate "Module output names" tab in the review workbook, cross-referenced back to
the original 190 rows by matching `(module, property, n, spearman, auroc)`. That gave every row its
real per-card `module_output` value, including a genuine `transform` note captured for `cdrHydro`'s
transformed card (`"Mahalanobis distance to a Gaussian distribution with μ=123.07, σ=16.87"` — the
first non-null transform note for that entry). Regrouping
`(module, param, column, module_output)` instead of the old 3-part key split all 6 merged entries
apart cleanly, zero unresolved conflicts. `seed/raw/module_evaluation_catalog.json` is now
**33 entries** (was 27) and `module_evaluation_details.json` is still 190 rows, fully unique on
`(module, param, column, module_output, property)`. Every catalog entry now also carries a
`module_output` field (added for all 33 entries, not just the 6 that needed it — useful,
human-readable display text worth carrying into the `Module`/`Metric` schema in migration 003).

One data point from this fix worth remembering: the SFvCSP raw/transformed pair have **identical**
Spearman (`0.199`, both) but **different** AuROC (`0.647` raw vs. `0.353` transformed — exact
complements). Confirms the earlier "EXACT_DUP is harmless" read was incomplete — Spearman alone
doesn't capture whether two cards are really the same data; AuROC (and presumably AuPRC/
Precision@Top5%) can still differ even when Spearman doesn't, because it isn't invariant the same
way under whatever transform AWS applies. Good thing these ended up as separate entries rather than
deduped away.

---

## 7. Schema follow-up: raw/transformed pairs need lineage + room for a correlation stat

`Metric`'s existing identity scheme (`app/models/orm.py`) already has the right shape for this —
`UniqueConstraint(module_id, column_key, variant_kind, variant)` plus a `VariantKind` enum
(`PARAMETER`, `MODE`, `SOURCE_MODEL`, `COMPONENT`) for "same column, different meaning" cases. It's
one member short: no `TRANSFORM`. The 6 raw/`-transformed` pairs fixed in §6 are exactly what this
axis was built for — import them as `variant_kind=TRANSFORM, variant="transformed"` (transformed)
vs. `variant_kind=TRANSFORM, variant=None` (raw), no constraint change needed. Scaffolded as a
YOUR-TURN comment on `VariantKind` in `orm.py`, including the Postgres gotcha: adding an enum member
to a live `SAEnum` needs `ALTER TYPE variantkind ADD VALUE 'transform'` by hand — Alembic
autogenerate won't emit it, and older Postgres can't add-and-use the new value in one transaction.

Two more things worth reserving room for now, before migration 003 is typed, so they're not an
afterthought later:

- **Lineage**: a self-referential `Metric.transform_of_metric_id` (nullable FK → `metrics.id`,
  `ondelete="SET NULL"`), set only on the transformed sibling, pointing back at its raw
  counterpart. Cheap to add, and without it there's no way to query "give me this metric's raw/
  transformed pair" — you'd have to re-derive it from `column_key` + `module_id` matching, which is
  exactly the kind of implicit, easy-to-get-wrong logic that caused the §6 bug in the first place.
- **Transform-correlation statistic**: `Metric.transform_stats: JSONB | None`, reserved for a
  correlation stat between a metric and its raw/transformed sibling (e.g.
  `{"spearman_vs_raw": ..., "n": ..., "computed_at": ...}`). This is **not** derivable from the
  Module Evaluation scrape — that only gives each metric's correlation against DPBD properties
  (`BenchmarkResult`), never one metric's values directly against its sibling's. It only becomes
  computable once real candidate data has both columns imported for the same candidates, so it
  stays `NULL` until then — same deferred-backfill pattern as `Metric.transform` already uses. The
  SFvCSP evidence in §6 (identical Spearman-vs-Titer, different AuROC-vs-Titer between the raw and
  transformed cards) is the concrete reason this is worth reserving rather than assuming the pair is
  redundant: two metrics that look like duplicates by one statistic can still diverge by another,
  and a direct raw-vs-transformed correlation is the only way to quantify that without an external
  property as an intermediary.

Both are documented as YOUR-TURN comments in `app/models/orm.py` now; no code or migration yet —
same "not typed until the GraphQL schema settles" deferral as the rest of migration 003.
