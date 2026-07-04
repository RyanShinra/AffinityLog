# First Real Run — Findings from `results.csv`

> Companion to `bio-discovery-scrape-handoff.md`. This is the first **real** Bio Discovery
> output (no longer synthetic). Recorded on branch `sample-run`, 2026-07-04. "Page 2" and
> revisions expected — this is a first pass, not a final reconciliation.

## The run

- **Recipe:** "De Novo Design" (Hosted, Amazon Bio Discovery, v1.0).
- **Target:** `1N8Z_chainC_HER2.pdb` — HER2 extracellular domain from PDB **1N8Z** (HER2 + Herceptin
  Fab co-crystal; chain C = antigen only), labeled chain **T**. (README + `orm.py` examples updated
  from the original `1S78` plan to `1N8Z`, 2026-07-04; the historical `claude-code-prompt.md` build
  prompt is left as-is, since it records what was originally asked.)
- **Framework:** `h-NbBCII10.pdb` — humanized camelid VHH nanobody (traces to PDB 3EAK,
  *Camelus dromedarius*; heavy-chain-only, no light chain).
- **Hotspot:** Rank 1 (residues 253–283, domain II / furin-like region; strongest IEDB epitope
  E=2.2e-13). Design loops trimmed to `H1,H2,H3` only (no L-loops — nanobody has no light chain).
- **Model sweep:** `[esm, amplify]` in List mode. **Cost: 1.00 EU** (of 5/month), ~12–22 min.
- **Output: 2 candidates**, AB00001 (Top-Tier / lead, from the **esm** subexperiment) and
  AB00002 (Strategic Trade-Offs, from the **amplify** subexperiment). See §7 — these are one
  candidate *each* from two swept subexperiments, not two designs from one run.

## Recipe module composition (from the recipe Overview screen)

Five modules, confirmed from the recipe's own tabs:

| Module | In our 33-entry scrape catalog? | Type |
|---|---|---|
| **RFantibody** | ❌ new (design module; the Module Evaluation page only benchmarked *scorers*) | Design and Score |
| **TemStaPro** | ✅ yes | Score |
| **PLM Pseudo-Perplexity** | ✅ yes | Score |
| **FastDPE** | ✅ yes | Score |
| **Boltz2** | ❌ new | Design and Score |

Module metadata now available to seed (fills fields that already exist in `orm.py`):
- **Boltz2**: v1.0, MIT License, `repo_url` → `github.com/jwohlwend/boltz` (the "Learn more" GitHub
  link on the module page — maps straight onto the existing `Module.repo_url` column).
- The Boltz2 module page's own blurb ("Extends Boltz-1 with joint affinity prediction") + the
  GitHub link is exactly the provenance trail Ryan flagged: the *display name* "Boltz2" is a
  Bio Discovery label; its real identity is the open-source Boltz-2 model. **The `module_output`
  ↔ real-tool mapping is worth tracing for all 42 palette modules** (see To investigate).

## Schema findings (the payoff)

### 1. A new `.H` / `.T` chain axis — biggest modeling decision
Export columns are `module.metric[.chain]`. Same metric computed per-chain:
- `temstapro.t50_raw.H` **and** `.T`; `temstapro.length.H`=127, `.T`=607
- `esm2pseudo_log_likelihood.pseudo_perplexity.H` — **`.H` only** (perplexity of the designed
  chain; scoring the antigen's naturalness is meaningless)
- `fastdpe.cdrHydro`, `fastdpe.SFvCSP` — **no chain suffix** (inherently antibody/CDR-specific)

Chain-suffix presence is **per-module, non-uniform** — same "parse defensively" gotcha as scrape
handoff §3, on a new axis. **Leaning:** model as `VariantKind.CHAIN` (`variant="H"`/`"T"`) — reuses
the existing `UniqueConstraint(module_id, column_key, variant_kind, variant)`, zero new schema,
same pattern as the `TRANSFORM` addition (§7). Alternative: chain as a first-class column on the
score row. **Defer the final call to the GraphQL design pass** (per the "GraphQL is the apparent
representation" principle) — the question is really "does a consumer query `thermostability` or
`heavyChainThermostability`?"

### 2. Non-scalar cell values — validates the `dict[str, str]` raw-scores decision
Real values that **cannot** be coerced to float, proving CLAUDE.md's "store raw strings, coerce
downstream" rationale correct:
- `temstapro.t50_binary.H` = `<40` (threshold string)
- `temstapro.t65_raw.T` = `-` (missing marker)
- `structure_analysis_rfantibody.epitope_residues` = `"249;250;259;..."` (semicolon list)
- `structure_analysis_boltz2.contact_by_cdr` = embedded JSON `{"159": {"H2": 15, "H3": 7}, ...}`

Importer note: `contact_by_cdr` (JSON) and `epitope_residues` (delimited list) are structured
sub-values, not scalars — `JSONB`/array sub-parses if ever made queryable.

### 3. `fasta_sequence` is a complex, not one sequence
The `sequence` cell is `nanobody / target` joined by `/`: `QVQL…VTVS/TQVCTG…CPIN`. The target half
carries the `XXXX` unresolved-residue runs. Importer currently assumes one sequence per candidate —
**broken assumption**.

### 4. Column-key reconciliation (`column_mapping.py`)
Real header is `esm2pseudo_log_likelihood.pseudo_perplexity.H` — not `pseudo_perplexity`
(our `column_key`) and not `PLM Pseudo-Perplexity` (the module *display* name). First confirmed
mapping to add.

### 5. Not every export prefix is a catalog Module
The CSV has `liabilities.*`, `cdr_indices.*`, `hdbscan.*`, `mmseqs.*`, `structure_analysis_{boltz2,
rfantibody}.*` — none of which are among the 5 recipe modules. These look like **built-in
analysis/pipeline passes** (developability liabilities, CDR indexing, clustering, structure
analysis) that run regardless of recipe, not palette modules. The schema shouldn't assume
`export column prefix == Module`.

### 6. Reconciled numbers
`structure_analysis_boltz2.t_length`=581 (modeled residues) vs `temstapro.length.T`=607 (full seq
incl. X's) — both earlier numbers were real, measuring different things. `boltz2.l_length`=0
confirms no light chain *in the data*.

### 7. List-mode sweeps → parent "bulk experiment" + N subexperiments (RESOLVED)
The `[esm, amplify]` sweep created **two subexperiments** under one parent bulk experiment
(`e3507…`), one candidate each — confirmed via the dashboard:
- AB00001 → `experimentId` `dc8e…f72c` → "Testing De Novo Design_1", Input param `Model = esm`,
  `pseudo_perplexity` 3.6637.
- AB00002 → `experimentId` `8ff8…2852` → "Testing De Novo Design_2", Input param `Model = amplify`,
  `pseudo_perplexity` 3.8768.

(Aside: esm scored *lower* perplexity than amplify here — directionally consistent with the
DPBD-derived expectation, though n=1 each, not conclusive.)

Three schema/importer consequences, all significant:

1. **Input params are NOT in the output columns.** The perplexity column is
   `esm2pseudo_log_likelihood.pseudo_perplexity.H` in *both* rows — including the amplify one. The
   column name is model-agnostic and effectively **misleading** (says "esm" for the amplify run).
   Which model produced a value is recoverable **only** via `experimentId` → the subexperiment's
   Input parameters. Per Ryan: outputs *might* be configurable to include input params, but at best
   that'd be an optional field — don't rely on it.
2. **Validates `Experiment.params` (JSONB).** `model=esm/amplify` is experiment-level run config,
   exactly what `Experiment.params` was designed for (its comment already lists `model_type`). This
   is a candidate's *experiment context*, not a candidate score — do **not** try to force it into
   the scores bag.
3. **One CSV export can span multiple experiments.** `results.csv` contains both subexperiments'
   candidates, distinguished by the `experimentId` column. The importer **cannot** assume
   `1 CSV = 1 experiment`; it must split/group by `experimentId`. This tensions with the REST design
   `POST /experiments/{id}/candidates/import` (implies one target experiment) — the `{id}` may need
   to be the parent bulk experiment, or the importer resolves per-row `experimentId`. **Flag for the
   GraphQL/REST design pass**, alongside the Candidate↔Metric question in `graphql-schema-handoff.md`.

### 8. The export is under-specified for provenance — AffinityLog's Experiment table IS the fix
The output CSV alone can't tell you *what experiment produced it or how it was configured*. Precisely:

- **What the CSV carries:** candidate scores, the `experimentId`, and — easy to miss — the target's
  full amino-acid **sequence** (the `/T` half of the `sequence` column; every `.T` metric is
  computed on it).
- **What it does NOT carry:** the target's **identity** (that it's `1N8Z`, chain C), and the run
  **config** — hotspots (253–283), framework (`h-NbBCII10`), model (esm/amplify). These live only on
  the experiment's GUI "Input parameters" panel, reachable via `experimentId`.

So candidate↔candidate comparison is easy; input→output provenance requires a join AffinityLog must
supply. **This is the reason the `experiments` table exists** — `Experiment.params` (JSONB: model,
hotspots, num_designs), `target_id`→`Target`, `recipe_id`→`Recipe`, `source_filename` are exactly
the provenance the export omits. AffinityLog *is* the join Bio Discovery's CSV doesn't provide, and
this validates the two-step REST design (`POST /experiments` with the known inputs, *then*
`POST /experiments/{id}/candidates/import`): step 1 exists **because** the CSV can't carry the inputs.

**The workflow constraint that makes this concrete (ties to the CLAUDE.md no-API rule):** the entire
Bio Discovery experiment setup is **GUI clicking** — no API, and no config-export button observed.
So the input values cannot be pulled programmatically; the operator must **manually transcribe** them
(target id, hotspots, framework, model) from the Bio Discovery GUI into a **companion input form/GUI
that AffinityLog provides**. That companion form is a real, required build item, not a nicety — it's
the only way the provenance ever enters the system. Design implications:
- The experiment-creation input shape (the fields the operator re-keys) is part of the GraphQL/REST
  API design pass — it's the human-facing half of "capture inputs at import time."
- Because one CSV spans multiple experiments (§7), the operator transcribes inputs **per
  `experimentId`** (e.g. the esm and amplify subexperiments are two Experiment records differing only
  in `params.model`), then the CSV rows attach to the matching record.
- A screenshot of the GUI "Input parameters" panel is literally the missing half of each import —
  worth keeping alongside the CSV until the companion form exists.

## To investigate (page 2)

- **The 42-module ↔ benchmark-catalog mapping.** Trace each of the 42 recipe-builder palette
  modules to (a) whether it appears in our 33-entry Module Evaluation catalog, and (b) its real
  upstream identity/`repo_url` (like Boltz2 → jwohlwend/boltz). Many "Design"/"Design and Score"
  modules never appeared on the benchmark page at all (it only benchmarked scorers).
- **`liabilities.*` as a module vs. built-in pass** — is it a catalog Module, or infrastructure?
- **How `temstapro`'s raw curve (`t40_raw`…`t80_raw`) relates to the benchmarked Tm1/Tm2/Tm3/onset
  properties** — the benchmarked properties are likely *derived* from the raw curve, not separate
  emitted columns. Affects how `Metric.transform` / benchmark linkage is modeled.
