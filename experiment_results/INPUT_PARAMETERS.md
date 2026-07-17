# Experiment Input Parameters — reconstructed provenance

The Bio Discovery CSV export does **not** carry a run's input configuration (models, seeds, hotspots,
hyperparameters) — that lives only in the console's "Input Parameters" panel (first-run-findings §8).
This file reconstructs it from the run sessions so it survives loss of console access.

**Confidence:** values below are from what was directly observed this session (a pasted panel for Run 9,
config/validate screenshots for the others). Treat as authoritative for models/seeds/sweeps/recipe;
verify exact numeric hyperparameters against saved screenshots where a decision hinges on them.
Companion: `CORPUS_MANIFEST.md` (ledger) · `file id mapping.xlsx` (ESM2 property↔sub-experiment).

## Run 1 — "Testing De Novo Design" (De Novo Design recipe)
- Target **1N8Z** chain C (HER2 ECD); framework **h-NbBCII10** humanized VHH nanobody.
- Hotspot rank 1 (residues **253–283**, IEDB E=2.2e-13); design loops H1,H2,H3 (no L — nanobody).
- Model sweep **[esm, amplify]** in List mode → 2 subexperiments. Cost 1.00 EU.
- (Full write-up: `docs/first-run-findings.md`.)

## Run 9 / exp_2 — "HER 2 Round 9" (recipe "HER2 Round 2 Again-copy-4") — panel pasted verbatim
EvoProtGrad: Protein Language Model **esm**; PLM weight **1**; Maximum mutations **15**; Custom Preserved
Regions **None**; Preserve CDR **false**; Preserve FR **false**; Output type **best**; Number of Steps **50**;
Parallel Chains **1**; Antibody Seed **seed_cand1_05f95b5a.fasta**.
PLM Pseudo-Perplexity: Model **amplify**. Description note: "Now without intellifold."

## exp_3 — "Experiment 3" (recipe "HER2 Round 3 take 1")
EvoProtGrad **esm**, seed **seed_cand1_05f95b5a.fasta**, mutations 15, steps 50, output best, chains 1,
preserve CDR/FR false. Recipe: EvoProtGrad → BioPhi → Humatch Classify, + TemStaPro, + Boltz2.

## Run 4 (FAILED) / "Her2 Round 4 - add intellifold" (recipe "HER2 Round 4")
EvoProtGrad **esm**, seed_cand1, **max mutations 5**, **steps 30**, output best, chains 1.
IntelliFold: Random Seed **42**, Recycling Iterations **1**, Diffusion Samples **2**, Sampling Steps **10**,
Precision **bf16**. Failed at runtime (sampling steps below IntelliFold's viable floor). No data produced.

## exp_5 — "Experiment 5 - Light and Heavy" (recipe "HER2 Round 3 take 1")
EvoProtGrad, seed **trastuzumab_HL.fasta**, Protein Language Model = List **[esm, amplify]** (sweep →
2 subexperiments), mutations 15, steps 50, output best, chains 1. Full recipe (as exp_3).

## exp_6 — "Experiment 6 - Match Heavy and Light to Target" (recipe "HER2 Round 3 take 1")
EvoProtGrad, seed **trastuzumab_HL.fasta**, PLM **esm** (single), otherwise as exp_5. Target chain (if any in
seed) dropped by EvoProtGrad ("Target chains are not evolved").

## Run 7 (FAILED) / "Experiment 7 - Dual evolution" (recipe "HER2 Round 7")
EvoProtGrad ← `evolved_HLT.fasta` (2nd-gen evolution); **standalone Boltz2** ← `evolved_humanized_HLT_domIV`.
Failed (disconnected-topology result-merge and/or severed-disulfide domIV construct). No data.

## exp_8 & exp_11 — "Her2 Round 8" / "Her2 Boltz2 Evolved" (recipe "Boltz2 Solo")
Boltz2 v1.0 (MIT), defaults, fed a complete H/L/T FASTA directly:
- exp_8 ← `evolved_humanized_HLT_dom4fix.fasta` (humanized) → iptm 0.702.
- exp_11 ← `evolved_HLT_dom4fix.fasta` (evolved) → iptm 0.793.
(HER2 target truncated to domain IV, residues 450–607, disulfide-safe cut. 354/355 residues, under Boltz2's 800-residue cap.)

## exp_9 & exp_10 — "ESM2 Prediction" / "ESM2 Evolved" (recipe "ESM2 Only")
ESM2 Property Predictor alone. `Fine-tuned Regression Model` = **List** of all 5:
thermal_stability, degradation_stability, solubility, immunogenicity, hydrophobicity (~0.1 EU each →
one sub-experiment per property; the property↔sub-experiment map is in `file id mapping.xlsx`).
- exp_9 ← `evolved_HL.fasta` (before/evolved) · exp_10 ← `evolved_humanized_HL.fasta` (after/humanized).
