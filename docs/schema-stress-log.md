# Schema Stress Log

> A running "learning summary" (the friendlier word for post-mortem 😄). One entry per real
> Bio Discovery run, framed around a single question: **what did we throw at the schema, what
> held, and what surprised us?** Companion to `first-run-findings.md` (the deep dive on Run 1)
> and `graphql-schema-handoff.md`. The point is to make schema evolution *evidence-driven* — we
> let real exports tell us what the model needs, rather than guessing up front.

Modules seen across all runs so far (of the ~42-module palette): **RFantibody, TemStaPro,
PLM Pseudo-Perplexity, FastDPE, Boltz2, EvoProtGrad, BioPhi, Nanobody Polyreactivity Scorer**
(8 cataloged) + **IntelliFold, Humatch Classify** (named but not yet in an export). Built-in
pipeline passes (NOT palette modules): `liabilities`, `cdr_indices`, `hdbscan`, `mmseqs`,
`structure_analysis_*`.

---

## Run 2 — "HER2 Round 9" (Experiment 2), 2026-07-15

**Config:** recipe "HER2 Round 2 Again-copy-4" ("Now without intellifold"), directed evolution
from `seed_cand1_05f95b5a.fasta`. Modules: EvoProtGrad, Boltz2, FastDPE, PLM Pseudo-Perplexity,
BioPhi, Nanobody Polyreactivity Scorer, TemStaPro. Runtime ~1h48m. Output: 110 columns, 2 rows.
CSV at `experiment_results/experiment_2/results (3).csv`. **(Re-read after Run 3: the 2 rows are 1
design + its BioPhi-humanized derivative, NOT 2 independent candidates — see Run 3's headline finding.
The "sparse row 2" is the humanized row, scored only by the humanness branch.)**

### What held
- **3 new modules (BioPhi, EvoProtGrad, Nanobody Polyreactivity Scorer) required ZERO DDL.** The
  003 catalog-plus-JSONB design absorbed them as pure data (new `modules`/`metrics` rows + `scores`
  JSONB). This is the redesign paying off exactly as intended. `Metric.column_key` is `String(128)`;
  longest real key (`esm_pseudolikelihood_ratio`) is nowhere near the cap. **Schema held.**

### What surprised us / confirmed a "parse defensively" rule
- **No `experimentId` column this run** (single experiment, not a list-mode sweep). Run 1 had it.
  → the importer **cannot assume** `experimentId` exists.
- **`.H` only — no `.T`** target-chain metrics, and `sequence` is a lone nanobody (not the
  `nanobody/target` `/`-joined complex of Run 1). Chain-suffix presence is per-export.
- **Provenance lives only in the Input Parameters panel, and the column names lie.** EvoProtGrad ran
  `esm`; PLM Pseudo-Perplexity ran `amplify` — yet the perplexity column is named `esm2pseudo_*`
  regardless. Consequence: **`evoprotgrad.esm_pseudolikelihood_ratio.H` is a PLAIN metric, NOT a
  `TRANSFORM` of `pseudo_perplexity`** (different modules, different models). We only know this from
  the input panel — validates `Experiment.params` and first-run §8. The `TRANSFORM` lineage built in
  003 has no real occupant yet.
- Built-in passes appear in **neither** module list → confirmed as pipeline infrastructure, not
  palette modules (first-run §5, reconfirmed).

---

## Run 3 — "HER2 Round 3 take 1" (Experiment 3), 2026-07-15 — *completed*

**Config:** EvoProtGrad (`seed_cand1`, esm, Output type `best`) → BioPhi (humanize) → **Humatch
Classify** (scores the *humanized* output). Plus TemStaPro, Boltz2. 95 columns, 2 rows. CSV at
`experiment_results/experiment_3/results (4).csv`.

### THE headline surprise: rows are pipeline *stages* of one lineage, not independent candidates
The 2 rows are the **same design at two stages**, 127 aa each, **5 mutations apart**:
- **Row 1** (`976cd559`) = EvoProtGrad's design. Populated by `evoprotgrad`, `temstapro`, `boltz2`.
- **Row 2** (`6d768eb0`) = **BioPhi's humanized version of Row 1.** Populated by `biophi` + `humatch`.
  `humatch.VH` is literally Row 2's own sequence, IMGT-aligned with `-` gap chars.

Modules **partition by which sequence they scored** — upstream scorers hit the original, humanness
tools hit the humanized derivative. `Output type: best` → 1 design → 1 humanized → 2 rows. **This is
the same pattern as Run 2's "sparse row 2"** — which we now understand was a humanized derivative, not
missing data. (Correct Run 2's entry accordingly.)

**Critical: nothing in the CSV links the two rows.** Different `id`s, no `parent_id`, no
`experimentId`. Columns equal across both rows are just built-in constants (CDR indices, liability
counts) that match because the sequences are 96% identical. **The design→humanized lineage is
inferable only from pipeline knowledge** (BioPhi's role), exactly like the provenance gap in
first-run §8. Built-in passes (`liabilities`, `cdr_indices`, `hdbscan`, `mmseqs`) run **per-row**.

### Schema consequences
1. **Empty ≠ missing; empty = "module didn't run on this sequence" (not-applicable).** Row 1's blank
   `biophi`/`humatch`, Row 2's blank `temstapro` are N/A, not failures. → loader must **omit empty
   cells** from `scores` JSONB — never store `''` or coerce to `0`.
2. **Candidate-level lineage is a schema GAP.** 003 models `TRANSFORM` at the *metric* level and
   H/L/T at the *chain* level, but has **no way to express "candidate B is the humanized form of
   candidate A."** OPEN DECISION: treat the two rows as independent Candidates (lose the lineage), or
   add a candidate self-referential link (mirror of the metric-level `transform_of_metric_id`). The
   link, if wanted, must be **synthesized by the importer** from pipeline knowledge — it's not in the data.
3. **Humatch mixes 3 value types in one module:** `CNN_H`=`0.99999976` (float), `hv`=`'hv3'`
   (categorical V-gene family), `VH`=aligned-sequence string. And **`CNN_L`/`CNN_P` are ABSENT, not
   empty** — the module emits columns only for chains present. Column sets are *content-dependent*
   (my "empty light-chain" prediction was wrong: absent, not blank).

### Concept-table fixture — and the two methods DISAGREE
On the same humanized sequence (Row 2): **BioPhi OASis** Percentile_After `0.16` / Identity_After
`0.68` (middling-human) vs **Humatch** CNN_H `0.9999` (near-certain human). Two modules, one
`humanness` concept, sharply divergent — the exact "do they agree?" question, answered *no*. First
real data to populate `Metric.concept_id → Concept`, and it immediately demonstrates *why* the
grouping is useful (surface both, let the consumer see the disagreement).

### Structural (consistent with Run 2)
- New module prefix `humatchclassify` = display name "Humatch Classify" lowercased, spaces stripped
  (same rule as `nanobodypolyreactivityscorer`, `biophi`). Useful `column_mapping` heuristic.
- `.H` only, no `.T`; no `experimentId` (single experiment).

---

## Run 4 — "Her2 Round 4 - add intellifold" (Experiment 4), 2026-07-15 — *launched overnight, awaiting export*

**Config:** Run 3 recipe **+ IntelliFold added** (structure module). EvoProtGrad now `amplify`
(was `esm`), max mutations 5, MCMC steps 30, Output `best`. IntelliFold cranked to minimum quality
for speed — Sampling Steps 10, Recycling 1, **Diffusion Samples 2**, bf16 → ~7–9h. Deliberately kept
**under 1.0 EU** (2 samples is the ceiling before it tips over) to bank credits for future
multi-permutation/sweep runs (the `experimentId` dark surface). Structures will be low quality by
design; we only want IntelliFold's **column shapes**.

**Predictions to check against the export:**
- New `intellifold.*` (+ likely `structure_analysis_intellifold.*` built-in pass) columns — possibly
  new nested/JSON/per-residue shapes like `structure_analysis_boltz2.contact_by_cdr` did in Run 1.
- **Diffusion Samples = 2:** does IntelliFold **fan out into 2 per-sample column sets (or 2 rows)**,
  or collapse to one? First test of a module emitting multiple samples per sequence.
- Boltz2 presumably still in → **two structure predictors on one design** → IntelliFold-vs-Boltz2
  confidence comparison (a structure-flavored twin of the Run 3 humanness `Concept` fixture).
- EvoProtGrad ran `amplify` but its column stays hard-named `esm_pseudolikelihood_ratio` → confirms
  the model-vs-column-name mislabel, now on **EvoProtGrad** (we'd only seen it on PLM Pseudo-Perplexity).
- Expect the same **design→humanized 2-row** structure as Runs 2–3 (EvoProtGrad→BioPhi→Humatch chain
  unchanged). Still `.H` only, no `experimentId` (single experiment).

---

## Run 8 — "Boltz2 HLT" standalone probe (Experiment 8), 2026-07-16 — *completed*

**Config:** Boltz2 **alone** (no other modules), fed `evolved_humanized_HLT_dom4fix.fasta` directly
(H=120, L=106, T=158 domain-IV construct, disulfide-safe cut at HER2 residue 450). 62 cols, 1 row,
~3 min compute (48 min wall — queued). Answered the Exp 7 question: **Boltz2 accepts a raw 3-chain
complex file** — a single-module recipe validated and ran fine. So Exp 7's failure was *not* the raw
multi-chain input; most likely the disconnected-topology / two-orphan-result-sets merge, or the
severed-disulfide construct it was fed. Both were confounded there; Run 8 removed both.

### Wins
- **First 3-chain candidate ever:** `sequenceType='H/L/T'`, `structure_analysis_boltz2` h/l/t = 120/106/158.
  Exercises `parse_chains()` at N=3 (identical code path to N=2 — as predicted, nothing new needed).
- **Real antibody–target interface score:** `boltz2.protein_iptm = 0.702` (moderate). Distinct from
  Exp 6's 0.95, which was only the H–L pairing. `ligand_iptm=0.0` (no small-molecule ligand).

### New schema surface
- **A TARGET unlocks columns — a third content-dependence axis.** Exports *with* a target (Run 1 H/T,
  Run 8 H/L/T) carry `tier`, `recommendation`, and **9** `structure_analysis_boltz2` columns (adding
  `epitope_residues`, `num_epitope_residues`, `min_interface_dist`, `contact_by_cdr`). Targetless runs
  (2/3/5/6) have **5** and no tier/recommendation. So column presence depends on: chains present
  (Humatch), input params (EvoProtGrad model), **and input composition (target present?)**.
- **`recommendation` = LLM-generated prose in a CSV cell** — a full natural-language paragraph, and it
  contains a **non-ASCII non-breaking hyphen (U+2011)** in "Top‑Tier". Brand-new value type: free text,
  non-ASCII, in a column that looks scalar. `dict[str,str]` + skip-empties absorbs it unmodified — but
  the encoding is a live warning for anything downstream that assumes ASCII.
- **`contact_by_cdr` (embedded JSON) shows L3 dominating the interface** (39/25/19 contacts) over H3 (22)
  — the paired antibody binds through *both* chains, structurally impossible for a nanobody. First data
  to show it.

### Ground-truth calibration
Boltz2's predicted epitope (construct offsets → HER2 numbering) recovers **5/13** real crystal contacts
(557,558,560,561,573) but drifts N-terminal and misses the 591–605 cluster. Plausible: this is a
two-generation-evolved, humanized sequence, not wild-type trastuzumab, so a shifted footprint is
expected. `iptm=0.70` is honestly moderate, not overconfident.

---

## Runs 9/10 — ESM2 Property Predictor, humanization before/after, 2026-07-16 — *launched*

**Question (science, not schema):** what did BioPhi's humanization do to *developability*? Pairs with the
Boltz2 binding comparison to give a 2x2 on the same molecule: {evolved, humanized} x {binding, developability}.

**Config:** recipe "ESM2 Only" — **ESM2 Property Predictor alone**, standalone (single-module recipes are
legal; Run 8 proved it). Two runs, each fed one HL-only seed built from Exp 6's exact chains:
- **before** = `round6_seeds/evolved_HL.fasta` (H=120, L=107)
- **after**  = `round6_seeds/evolved_humanized_HL.fasta` (H=120, L=106 — humanization dropped a residue)

Target deliberately omitted — this scores the antibody, not the complex.

**Why two runs, not one pipeline:** a scorer only sees the branch it's wired to (TemStaPro scored the design
row, Humatch the humanized row), and module-uniqueness forbids a second instance. So one pipeline run cannot
produce ESM2PP scores on *both* stages. Two standalone runs also score the *exact* Exp 6 molecules, lining the
developability comparison up with the binding one.

**`Fine-tuned Regression Model` is a List-mode enum** (multi-select): `thermal_stability`,
`degradation_stability`, `solubility`, `immunogenicity`, `hydrophobicity`. **Costed at ~0.1 EU per property**
(2 checked = 0.2), so ~0.5 EU for all five — i.e. **the column set is chosen by an input param**, a sharper
version of the EvoProtGrad esm/amplify finding: here the *operator* picks which columns exist.

**Predictions to check:**
- **`immunogenicity` should drop** after humanization — that is literally humanization's purpose. If it
  doesn't, something's wrong with the run or the humanizer.
- **`hydrophobicity` / `solubility`** — the genuinely open question ("did we pay for humanness with
  developability?"). Human frameworks are usually well-behaved, but a framework swap can expose a patch.
- **The light chain should move more than the heavy** — it took the indel (107 -> 106).
- **`thermal_stability` is a Concept-table fixture**: same concept as TemStaPro's thermostability, different
  module. Second real occupant for `Metric.concept_id` after humanness (BioPhi vs Humatch).
- **Export shape unknown:** if each property is its own subexperiment, each run may return **N `experimentId`s**
  (discriminator = regression head, not PLM) rather than one row with five columns. Either shape loads fine via
  `group_rows_by_experiment` — but it would be a *new flavour* of swept dimension.

**Queue vs compute (operational note):** estimates quote *compute*, not wall time. Run 8: 2-3 min quoted, 48 min
actual. These: 1-2 min quoted, 40+ min and counting. The gap is queueing.

### RESULT (Run 9, evolved/before) — the strongest "column name is not enough" case in the whole corpus
45 cols, **5 rows = 5 `experimentId`s** (one subexperiment per checked property — the discriminator is the
regression head). **The output column is fully generic: `esm2propertypredictor.predicted_property.H` / `.L`**
— NOT named by property. All five properties report into the *same* column name; which row is
hydrophobicity vs solubility vs immunogenicity vs thermal vs degradation is **not in the CSV at all**. It is
recoverable *only* via `experimentId` → the subexperiment's `Fine-tuned Regression Model` input param.

This is `model_type=esm/amplify` (first-run §7) taken to the limit: there the column at least said `esm2pseudo`;
here it says nothing distinguishing. **Consequences:**
- **Definitive proof** that metric identity must be `(module, column, discriminator)` and that the discriminator
  can be an input param recoverable only from `Experiment.params`. The five numbers are meaningless without the
  provenance join — this is the sharpest possible validation of first-run §8 and the whole reason `experiments`
  exists.
- **Operational:** the property label MUST be transcribed from the GUI per subexperiment; the export can't carry
  it. (Order *might* follow checkbox order thermal/degradation/solubility/immunogenicity/hydrophobicity, with
  `mmseqs.index` 0-4 as a hint — unconfirmed; needs the params panel.)
- Built-in passes (`cdr_indices`, `liabilities`, `hdbscan`, `mmseqs`) re-ran identically on all 5 rows (same
  antibody, so identical values) — confirms built-ins run per-row regardless of the swept param.
- `esm2propertypredictor.name.H/.L` = the chain label (`H`/`L`) — redundant with `sequenceType`, a no-op column.
- Loads fine (5 Experiments via `group_rows_by_experiment`), but the generic key means the *same* `scores` key
  appears across all 5 Experiments — cross-experiment comparison REQUIRES the params-side property label.

### THE PAYOFF — the provenance join caught a real analysis error (Runs 9 vs 10, corrected via the "Rosetta Stone")
Operator built `experiment_results/file id mapping.xlsx` by hand, correlating each candidate/sub-experiment ID to
its property (the transcription first-run §8 said was unavoidable). It revealed two things:
1. **The property↔sub-experiment-order mapping is INCONSISTENT between runs.** Exp 9: hydrophobicity = ordinal 4,
   thermal = ordinal 1. Exp 10: hydrophobicity = ordinal 1, thermal = ordinal 5. So you **cannot** align the two
   exports by row order (`mmseqs.index`) — doing so pairs *different properties* against each other.
2. A first-pass analysis that aligned by row order produced large, plausible-looking, **wrong** deltas
   (e.g. "immunogenicity −0.22, hydrophobicity +0.53"). Matched correctly by property via the mapping, the real
   deltas are all < ~0.05 except `thermal_stability.L` (−0.16). **The dramatic story was a misalignment artifact.**

**This is the strongest validation in the whole corpus of why `Experiment.params` / the provenance join exists:**
without it, a CSV-only pipeline aligns "5 rows of `predicted_property`" by position and ships confident, wrong
answers. The join is the artifact that makes correct cross-experiment analysis *possible*, not just prettier.

**Corrected science result (humanization, evolved → humanized):** developability barely moved (all |Δ| < 0.05
except thermal_stability.L −0.16 — the light chain took the indel); the "immunogenicity should drop" prediction
did **not** clearly hold. Binding was the clearest effect: Boltz2 `protein_iptm` 0.793 → 0.702 (−0.09). Net: a
small humanness-for-affinity trade, with modest developability impact. (Property *directions* still un-transcribed;
magnitudes are small enough that it barely matters.)

---

## Provenance recovery — the HTML-scraper capstone (2026-07-17)

The generic-`predicted_property` problem (Run 9/10) forced the provenance for the ESM2 sweeps to be
recovered **by hand** — clicking into all ten sub-experiments and transcribing candidate↔property into
`experiment_results/file id mapping.xlsx`. We then proved that hand-work is automatable:

- **`scripts/scrape_input_parameters.py`** — parses a browser-saved page (stdlib only, no deps). An
  Overview page → the Input Parameters table as JSON; a Results page → the candidate↔sub-experiment
  links. Validated on Exp 5: all 4 candidate→sub-experiment links recovered from the DOM matched the
  mapping independently derived from the CSV's `experimentId` column.
- **`scripts/rebuild_rosetta_stone.py`** — composes the two (per-sub-experiment property from Overview
  + candidate from Results) into candidate→property, and **self-checks against the xlsx: 10/10 exact**.
  The AI-written scraper reproduces the hand-built spreadsheet and proves itself correct against the
  human oracle.

**The scrape-vs-compute dividing line (a durable principle):**
- **Scrape what the CSV *omits*** — input parameters, the candidate↔sub-experiment↔property mapping.
  This genuinely isn't in the export; it lives only in the console DOM (or a manual transcription).
- **Compute what the CSV *implies*** — e.g. the **CDR positional grid** is just `sequence` sliced by
  the `cdr_indices.*` boundaries (both in the export). Reconstructed it directly; CDR-H3 cleanly
  separates both experimental axes (design S→A on humanization; esm `GFYAMDY` vs amplify `GSYALDY`).
  No scraping needed — a natural GraphQL computed field for later.

**The one remaining MANUAL step (honest future work):** capturing the HTML itself — the per-page
`ctrl-S` save. Parsing is solved; *capture* is not. In a real setting that's a headless-browser /
browser-automation loop (or Claude-in-Chrome). It's the only un-automated link in the
capture → scrape → structured-data pipeline — genuine "that's what interns are for" toil.

---

## Cross-cutting design findings

### Modules disagree on how to represent an ABSENT chain (found 2026-07-16)
The biology: a **nanobody (VHH) is heavy-chain-only — 0 light chains**; a conventional antibody
(trastuzumab) has **1 H + 1 L**. Every run so far seeded a nanobody, which is *why* all our data is
`.H`-only. This is exactly the premise `CandidateChain` encodes ("0..N chains of each type") — the
nanobody-vs-mAb distinction, not abstract over-engineering.

Two modules represent "no light chain" **differently, in the same CSV**:
- `cdr_indices` → emits **6 heavy + 6 light columns always**; the light ones are **present but EMPTY**
  on every nanobody run (Runs 1–4). Fixed column set.
- `humatchclassify` → **omits `CNN_L`/`CNN_P` entirely** (Run 3). Content-dependent column set.

**Consequence:** the loader can't assume either convention — column-set behavior is **per-module**, not
per-export. Third axis of the "parse defensively" rule (after chain-suffix presence and empty≠missing).

**Live prediction:** on the trastuzumab (paired H+L) run, `cdr_indices.light_cdr*` should populate for
the first time ever, and Humatch should finally emit `CNN_L`/`CNN_P`. `light_cdr1_start` is the
one-glance smoke test that the light chain flowed end-to-end.

### Column sets depend on INPUT PARAMS, and a parent export UNIONS them (found 2026-07-16, Exp 5)
Run 5 (paired H/L, list-mode sweep `[esm, amplify]`, 138 cols, 4 rows) emits **ten** EvoProtGrad
columns — including a pair we'd never seen:

    evoprotgrad.esm_pseudolikelihood_ratio.H / .L
    evoprotgrad.amplify_pseudolikelihood_ratio.H / .L

Each row populates **only the pair matching its own PLM**: `a37ae2cb` fills `amplify_*` (esm_* blank),
`0bbf19fe` fills `esm_*` (amplify_* blank). Two consequences:

1. **EvoProtGrad names columns by model — so the model IS recoverable from the data alone** (which
   column filled), with no `experimentId`→params lookup. This **refines first-run §7**, which is true
   only for **PLM Pseudo-Perplexity** (`esm2pseudo_log_likelihood.*` is named that way regardless of
   the model that ran). **Two modules, opposite conventions.** Do not generalize either one.
2. **A combined/parent export UNIONS columns across subexperiments.** The column set is a superset of
   every subexperiment's; each row fills only its own subset.

**So "empty" has THREE distinct meanings**, all collapsing to the same blank cell:
   (a) this module didn't run on this sequence (design vs humanized row);
   (b) this column describes a chain that doesn't exist (nanobody + `cdr_indices.light_*`);
   (c) this column belongs to a different subexperiment's parameterization.
All three are handled correctly by skip-empties — the loader stores presence, never absence.

**Corollary (a missed opportunity):** a third sweep value (`igbert`) *would* have added real
`evoprotgrad.igbert_pseudolikelihood_ratio.*` columns. It was argued down on the false premise that
EvoProtGrad's naming was model-agnostic — a generalization drawn from Run 4, which **failed** and
therefore never produced amplify output to check.

### `sequenceType` is the chain-label key (found 2026-07-16) — the export DOES tell us the layout
`sequenceType` holds the chain labels for the `/`-joined `sequence` column, **positionally, per row**:
- Run 1: `sequenceType='H/T'` ↔ `sequence` splits into 2 parts (127 aa nanobody, 607 aa HER2 target).
- Run 3: `sequenceType='H'` ↔ 1 part.

**Consequence:** the loader never guesses chain order — `zip(sequenceType.split("/"), sequence.split("/"))`
yields `(label, sequence)` pairs straight into `CandidateChain`. No pipeline knowledge required. This is
the *opposite* of the candidate-lineage gap (which genuinely isn't in the data) — chain layout **is**.

**Tooling note:** `scripts/split_sequences_to_fasta.py` takes a manual positional `--chains` flag and
warns it "must match how that particular export lays its chains out." That flag is **unnecessary** — the
script could read `sequenceType` per row. Worth fixing.

### `experimentId` presence rule (found 2026-07-16)
Run 1 produced three files, and they differ:
- `results.csv` — the **combined/parent** export of the sweep: **has `experimentId`**, 2 distinct values
  (one per subexperiment).
- `results (1).csv` / `results (2).csv` — **per-subexperiment** downloads: **omit the column entirely**.

**Rule:** parent/combined export → `experimentId` present (group rows by it → N Experiments);
single-subexperiment or non-swept export → absent (whole CSV = 1 Experiment). This is why Runs 2–3 had
none — single experiments have no parent to combine.

### Scores-JSONB keying (decision pending, but sharpening)
- Options: **A** = full CSV header as key (`esm2pseudo_log_likelihood.pseudo_perplexity.H`),
  **B** = bare metric (`pseudo_perplexity`), **C** = nested by module.
- **B is already dead:** Run 1's `temstapro.length.H` vs `.T` collapse to one key. Lossy on data we
  already hold.
- **Bio Discovery enforces module uniqueness per recipe** (the GUI won't let you drop the same module
  in twice). → Within a single experiment, a module prefix is **unique by construction**; intra-
  experiment header collisions are impossible. Headers only repeat *across* experiments in one CSV
  (the `experimentId` case). **So the effective key is `(experimentId, full header)`** — full header
  unique within an experiment (platform-guaranteed), `experimentId` disambiguating the multi-
  experiment CSV. This strongly favors **option A**. Final call deferred until we have Humatch data in
  hand, but the platform constraint did most of the work for us.

### Loader status
- **No loader exists.** The entire app layer above the ORM was cleared for the schema-first redesign
  (`csv_importer.py`, `column_mapping.py`, `routers/experiments.py`, `graphql/{types,schema}.py`,
  `schemas/pydantic.py` are all 2-line tombstones). They point at a `v0-scaffold` tag that **was never
  actually created** — the real baseline is commit `6d40429`.
- The old baseline loader was built for the **old** schema (first-class `humanness_score`/
  `binding_affinity_kd` columns + a separate `raw_scores` bucket). Both are gone in 003 — it's a
  reference for CSV-parsing mechanics only, not a salvage. The new model is *simpler*: one `scores`
  JSONB bag, no field routing.

### FASTA/PDB chain vocabulary (from the Experiment-3 configure screen)
- Sequence descriptions / PDB chain IDs must be a **single char: `H` (heavy), `L` (light), `T`
  (target)** — one antibody per file, optional target. Confirms the `Chain` enum vocabulary (H/L/T)
  our 003 `CandidateChain` uses.

### One score key, three physical meanings — resolved by a sibling table (found 2026-07-31)

**How we found it (worth keeping for the portfolio narrative — this was an *iteration*, not a plan).**
The demo page had three hardcoded candidates and looked finished. Widening it to every candidate with
a structure on disk (3 → 9) was supposed to be a mechanical change. Reading the widened result set is
what exposed the bug: five of the nine had **no `TARGET` chain at all**, yet were carrying the highest
"binding" scores on the page.

**The finding.** `boltz2.protein_iptm` is a single, stable JSONB key that measures a physically
different thing depending on which chains went into the fold — and **the bag does not record which**:

| chains folded | n | what ipTM actually measures | observed range |
|---|---|---|---|
| `HEAVY` | 4 | nothing — one chain has no interface | 0.000 |
| `HEAVY/LIGHT` | 6 | the antibody's own heavy–light **pairing** | 0.948 – 0.962 |
| `HEAVY/LIGHT/TARGET`, `HEAVY/TARGET` | 4 | genuine antibody–HER2 **binding** | 0.196 – 0.793 |

The three bands **do not overlap**, and they rank exactly as the physics demands: H/L pairing is nearly
deterministic (those chains evolved to snap together), so it scores ~0.95, while real binding is the
hard prediction problem and scores far lower. **Consequence: a naive "sort by ipTM" ranks every
target-less fold above every real HER2 complex.** The column name (`binding_iptm`) was actively lying
on 10 of 14 rows.

**The fix.** Meaning was recovered from a **sibling table, not the bag** — `candidate_chains` knows
which chains exist, so a `CASE` over `bool_or(cc.chain = 'TARGET')` in `candidate_summary` derives an
`interface_kind` label per row, and `binding_iptm` was renamed to the honest `iptm`. This is the same
lesson as §"Column sets depend on INPUT PARAMS" one level up: **the score bag is never self-describing;
its neighbours are what interpret it.** The normalized `candidate_chains` table earning its keep.

**Verified, not assumed:** all 9 PDBs' `ATOM` chain IDs were checked against `candidate_chains` — 9/9
match, so the classification isn't running off stale metadata.

**Residual limits (do NOT over-claim):**
1. ipTM is **assembly-wide**, so even within `antibody-target complex` a 2-chain `H/T` nanobody and a
   3-chain `H/L/T` fold aren't strictly comparable — different interface counts feed one statistic.
   Comparisons are only clean between rows with the *same* chain composition.
2. `interface_kind` infers what the score measured from sibling chain rows. If a recipe ever emitted an
   ipTM computed over a *subset* of the chains present, the label would lie silently. The real fix is
   **recipe-DAG provenance** (which module consumed which chains) — see `recipe-topology-note.md`.
3. `bool_or` over an empty group returns **NULL, not false** (LEFT JOIN), which would fall through the
   `CASE` and mislabel a chainless candidate — guarded with a leading `count(cc.id) = 0` branch.

---

## Doc debt this surfaced (to reconcile)
- **`CLAUDE.md` "Data model rationale" is stale** — still describes `binding_affinity_kd`,
  `humanness_score`, `aggregation_propensity` as first-class indexed columns. 003 removed them;
  confirmed in `orm.py` and the live DB (`candidates` has only `scores jsonb` + a GIN index).
- **`orm.py`'s example comment** `WHERE (scores->>'pseudo_perplexity')::float < 10` implies bare-key
  (option B) storage — becomes stale the moment we commit to full-header keying (option A).

## DB / environment
- PC dev database upgraded **001 → 003 (head)** this session (was stranded at 001 on an old persisted
  Docker volume; 002/003 had only ever run in testcontainers). DB was empty, so 003's
  `drop_column("candidates","fasta_sequence")` lost nothing.
