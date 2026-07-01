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

- `Metric.benchmark_stats: JSONB, nullable` — validate through a Pydantic model before write, then
  store the dump. Don't let the app write arbitrary keys:

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
