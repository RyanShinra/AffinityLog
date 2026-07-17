# Bio Discovery Experiment Corpus — Manifest

Provenance index for the AffinityLog experiment corpus, reconstructed from the run sessions
(2026-07-15/16). **Purpose: preserve the console-only context** (recipes, models, seeds, what each
run produced) independent of the Amazon Bio Discovery console, in case access is revoked. The scored
data itself is the per-experiment CSVs; this file is the map to them.

- **Project:** "My First Project" — ID `715ffe4c540b4f0dae2c402570ecbe85`
- **Target throughout:** HER2 / PDB **1N8Z** (Herceptin Fab–HER2 co-crystal; chain C = antigen)
- **Full findings:** `docs/schema-stress-log.md`. **ESM2 property → sub-experiment mapping:** `file id mapping.xlsx`.

| # | Console name | Recipe | Status | Dir | Seed / input | Produced |
|---|---|---|---|---|---|---|
| 1 | Testing De Novo Design | De Novo Design | ✓ | `experiment_1` | h-NbBCII10 nanobody framework, esm/amplify **sweep** | 2 subexp (H/T), 1 cand each; `.T` + experimentId |
| 2 | HER 2 Round 9 | HER2 Round 2 Again-copy-4 | ✓ | `experiment_2` | `seed_cand1` nanobody | design + humanized (H); 3 new modules |
| 3 | Experiment 3 | HER2 Round 3 take 1 | ✓ | `experiment_3` | nanobody seed | design + humanized (H); Humatch, Concept fixture |
| 4 | Her2 Round 4 - add intellifold | HER2 Round 4 | ✗ FAILED | — | nanobody | none (IntelliFold gutted below viable floor) |
| 5 | Experiment 5 - Light and Heavy | HER2 Round 3 take 1 | ✓ | `experiment_5` | `trastuzumab_HL.fasta`, esm/amplify **sweep** | 4 rows = 2 subexp × (design+humanized); **first `.L`** |
| 6 | Experiment 6 - Match H&L to Target | HER2 Round 3 take 1 | ✓ | `experiment_6` | `trastuzumab_HL.fasta` (target stripped by EvoProtGrad) | design + humanized (H/L) |
| 7 | Experiment 7 - Dual evolution | HER2 Round 7 | ✗ FAILED | — | `evolved_humanized_HLT_domIV` | none (disconnected topology + severed-disulfide construct) |
| 8 | Her2 Round 8 | Boltz2 Solo | ✓ | `experiment_8` | `evolved_humanized_HLT_dom4fix.fasta` | 3-chain H/L/T; **humanized binding** iptm **0.702** |
| 9 | ESM2 Prediction | ESM2 Only | ✓ | `Experiment_9` | `evolved_HL.fasta` (**before/evolved**) | 5 subexp (1 per property); generic `predicted_property` |
| 10 | ESM2 Evolved | ESM2 Only | ✓ | `experiment_10` | `evolved_humanized_HL.fasta` (**after/humanized**) | 5 subexp (1 per property); generic `predicted_property` |
| 11 | Her2 Boltz2 Evolved | Boltz2 Solo | ✓ | `experiment_11` | `evolved_HLT_dom4fix.fasta` | 3-chain H/L/T; **evolved binding** iptm **0.793** |

**Recipe module compositions (from the recipe canvases):**
- **De Novo Design:** RFantibody, TemStaPro, PLM Pseudo-Perplexity, FastDPE, Boltz2.
- **HER2 Round 2 Again-copy-4** (Run 9): EvoProtGrad (esm), PLM Pseudo-Perplexity (amplify), BioPhi, Nanobody
  Polyreactivity Scorer, TemStaPro, FastDPE, Boltz2.
- **HER2 Round 3 take 1** (Exp 3/5/6): EvoProtGrad → BioPhi → Humatch Classify, + TemStaPro, Boltz2 (all off EvoProtGrad).
- **Boltz2 Solo** (Exp 8/11): Boltz2 alone, fed a complete H/L/T FASTA directly.
- **ESM2 Only** (Exp 9/10): ESM2 Property Predictor alone; `Fine-tuned Regression Model` = List of 5 properties
  (thermal_stability, degradation_stability, solubility, immunogenicity, hydrophobicity) → one sub-experiment each.

**Key results (headline science):** humanization of the evolved anti-HER2 antibody cost ~0.09 binding
confidence (Boltz2 iptm 0.793 → 0.702) for a small developability shift (all ESM2 property |Δ| < 0.05 except
light-chain thermal_stability −0.16). The ESM2 "before/after" is ONLY interpretable via `file id mapping.xlsx`
(the export's `predicted_property` column is generic — see schema-stress-log).

**Still to capture before access loss:** each experiment's Input Parameters panel (screenshot — not
downloadable). **Structure PDBs ARE downloadable** (candidate → Structure Preview → Download, named
`<candidateID>_boltz2.pdb`, B-factor = per-residue pLDDT); Boltz2 folds exist for exp 1/2/3/5/6/8/11.
Target-complexes are the prizes: Run 1 (nanobody–HER2), exp_8 (humanized), exp_11 (evolved — filed).

**Seed FASTA provenance:** `round5_seeds/` (trastuzumab VH/VL from 1N8Z), `round6_seeds/` (evolved chains from
Exp 6 ± HER2 domain-IV target, disulfide-safe cut at residue 450 = the `*_dom4fix` files). Full-length `*HLT`
files are pre-cap dead-ends kept for the record; the two `*_domIV` files (severed disulfide) were deleted.
