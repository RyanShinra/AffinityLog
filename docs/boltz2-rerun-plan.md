# Re-running Boltz-2 locally to recover the per-interface scores

**Status: not started. Written 2026-11-03, from a cloud session with no GPU.** Everything here was
read out of `jwohlwend/boltz` at `main` and checked against this repo's corpus. Nothing has been
run. Two runs, on hardware this project already has.

---

## The question

`boltz2.protein_iptm` is aggregated over every interface in a fold. For the two three-chain folds
in the corpus — runs 8 and 11, the humanization pair — that means the antibody's own heavy–light
interface is averaged in with the antibody–HER2 one. So neither 0.702 nor 0.793 is a binding
number, and **the project's headline result is a difference of two blends**.

Boltz-2 already computes the breakdown. `compute_ptms` in
`src/boltz/model/layers/confidence_utils.py` returns `pair_chains_iptm` — a score for every pair of
chains — in the same forward pass as the aggregate, and `confidence.py` writes it into the
confidence JSON. The Bio Discovery export carries `iptm`, `protein_iptm`, `ligand_iptm`, `ptm`,
`complex_plddt`, `complex_iplddt`, `complex_pde`, `complex_ipde`, `confidence_score`, `name` and
`structureFile`. It does not carry `pair_chains_iptm` or `chains_ptm`.

Running the two folds locally recovers H↔T on its own.

---

## Why it is worth doing

1. **It turns the headline into a real number.** "Humanization cost 0.09 of binding confidence" is
   currently measured on a blend. The honest version is the cost on the H↔T interface alone, and it
   could be larger, smaller or the other way round.
2. **It tests whether the schema is right or only adequate.** The design says the discriminator is
   derived from chain composition. If the upstream model emits per-interface scores, the ideal
   schema might store one row per interface instead. Worth knowing which this is.
   **The design survives either answer.** `pair_chains_iptm` is keyed by chain index and never says
   which chain is the target, so `candidate_chains` and the `CASE` are still what supply the
   meaning. The likely outcome is "both", which is a better story than the deck tells now.
3. **It makes the flattening an exhibit rather than a citation.** Afterwards there are two files on
   disk: Boltz-2's confidence JSON with the matrix, and the Bio Discovery CSV without it.
4. **It is a cheap first pass at [`published-data-goal.md`](published-data-goal.md)** — the same
   science from a different producer, in a different output shape, against the existing importer.

## What it will NOT establish

- **It will not reproduce the Bio Discovery numbers.** Different MSA, seed, sampling parameters and
  possibly a different model build. The claim afterwards is "an independent run of the same model
  on the same sequences gives X", never "run 8 is verified".
- **No affinity.** Boltz-2's affinity head takes a small-molecule ligand against a protein target.
  An antibody–protein complex is out of scope; the code will run and the output is unreliable
  (`docs/prediction.md`, "Properties (affinity)").
- **No wet-lab truth.** Everything stays predicted.

---

## Inputs — already in this repo, complete

| run | file | chains |
|---|---|---|
| 8, humanized | `experiment_results/round6_seeds/evolved_humanized_HLT_dom4fix.fasta` | H 166, L 166, T 158 |
| 11, evolved | `experiment_results/round6_seeds/evolved_HLT_dom4fix.fasta` | H 167, L 167, T 158 |

Roughly 490 residues per fold. Both are plain three-protein FASTAs with `>H`, `>L`, `>T` headers.
The corpus values to compare against are `iptm` 0.702 (run 8) and 0.793 (run 11); both have
`ligand_iptm` 0.0, so `iptm` and `protein_iptm` are identical throughout.

## What is needed beyond the repo

- **Weights** — downloaded to `~/.boltz` on first run. Not in the source checkout.
- **An MSA** — `--use_msa_server` generates one via ColabFold, which needs network. A precomputed
  `.a3m` per chain works offline.
- **A GPU.** The PC's 16 GB card should hold ~490 residues. CPU runs and is much slower.
- Boltz's FASTA format is deprecated in favour of YAML but still supports plain polymers, which is
  all these two need. YAML is the safer choice if anything misbehaves.

## The run

```bash
pip install boltz[cuda] -U
```

```bash
boltz predict experiment_results/round6_seeds/evolved_humanized_HLT_dom4fix.fasta --use_msa_server --out_dir ~/boltz-rerun/run8
```

```bash
boltz predict experiment_results/round6_seeds/evolved_HLT_dom4fix.fasta --use_msa_server --out_dir ~/boltz-rerun/run11
```

## What to capture

From `predictions/<name>/confidence_<name>_model_0.json` in each output directory:

- `pair_chains_iptm` — **the point of the exercise.** A nested dict, chain index to chain index.
  Chain order follows the FASTA, so 0=H, 1=L, 2=T, and the number wanted is `[0][2]` / `[2][0]`
  with `[1][2]` as the light-chain contribution.
- `iptm`, `protein_iptm`, `ptm`, `complex_plddt`, `confidence_score` — to see how far the local run
  lands from the console's.
- `chains_ptm` — per-chain fold quality, also absent from the export.

Keep both JSON files. They are the exhibit.

## Reading the result

Let `b` be `pair_chains_iptm[0][2]`, the antibody heavy chain against HER2.

- **If `b` is well below the aggregate** (the estimate from the corpus is somewhere near 0.45
  against a reported 0.702, arrived at by assuming the heavy–light term sits near 0.95 and the
  residue counts split the mean roughly evenly) — then the aggregate was substantially inflated by
  the easy interface, and every "binding confidence" figure in the deck needs the caveat stated out
  loud rather than in a footer.
- **If `b` is close to the aggregate** — the blending is real but small, the existing numbers stand
  as written, and the caveat can stay a footnote.
- **Either way, compare run 11's `b` against run 8's.** That difference is the real cost of
  humanization, and it replaces the 0.09 currently on the humanization slide.

Do not write the ~0.45 estimate into the README or the deck before this runs. It is an inference
from how the aggregate is computed, not a measurement, and it rests on borrowing a heavy–light
figure from a different fold whose scoring tolerance was narrower.

## If it changes the schema's story

Record it in [`metric-heading-normalization.md`](metric-heading-normalization.md), which already
holds the open question about making interface identity structural rather than a write-time check.
A per-interface score from upstream is new evidence for that argument.

---

## Why the tolerance detail matters here

`tm_function` is:

```python
d0 = 1.24 * (clip(Nres, min=19) - 15) ** (1/3) - 1.8
return 1 / (1 + (d / d0) ** 2)
```

`Nres` is the whole assembly, so `d0` grows as chains are added and the scoring gets more forgiving
for every pair, including ones that were already there. Measured: a 5 Å predicted error scores
0.639 on the 332-residue H/L fold and 0.713 on the 490-residue H/L/T fold. This is the second
reason ipTM values do not travel between folds of different composition, and it is why the
heavy–light figure borrowed from an H/L-only fold cannot simply be subtracted out on paper.

The aggregate itself is a mean then a max: for each residue, the mean expected TM over every
residue in a *different* chain, then the maximum of that over all residues. It is therefore
weighted by residue count rather than by interface, and it reports the single best-anchored
residue's view of the rest of the assembly.
