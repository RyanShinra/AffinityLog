# The biology behind the demo — what we think we're looking at

This is the science narration for the `/demo` page (the three featured antibody–HER2
complexes and the provenance panel). Written to be readable by a software engineer with no
biology background — the whole conceit of AffinityLog is a backend that happens to sit on
antibody-design data, so the data engineering stands on its own, but this is the "why it's
cool" layer.

**Every number here is a computational _prediction_** (Boltz2 structure prediction, BioPhi/
Humatch humanness models), not a wet-lab measurement. Read the [Caveats](#caveats) before
repeating any of it as fact.

---

## The target: HER2

HER2 (gene *ERBB2*) is a growth-signal receptor on the cell surface. In roughly 1 in 5 breast
cancers it is massively overexpressed, which drives the tumor. It is the target of
**trastuzumab (Herceptin)**, one of the most successful antibody drugs ever made.
Trastuzumab binds **domain IV** of HER2's extracellular region — which is the fragment we
truncated the structure down to before folding (see the 800-residue Boltz2 cap in
`docs/schema-stress-log.md`). So the **red epitope patch** in the 3D viewer is the model's
prediction of *where on HER2 the antibody grabs*; for a trastuzumab-lineage binder it should
sit on domain IV, which is the by-eye sanity check.

## The binders: two shapes of antibody

- **Full antibodies** (the `H/L/T` tabs) — the classic "Y". Two chains do the binding: a
  **heavy (H)** and a **light (L)**; their tips form the *paratope* that grips the target.
  Both trastuzumab-line molecules are this shape.
- **The nanobody** (the `H/T` tab) — a **single-domain** antibody (a VHH, the kind camelids
  make). No light chain at all, hence `HEAVY/TARGET`. This one was **designed de novo**
  against HER2 rather than derived from a known drug. It is the hardest ask of the three, and
  its low predicted interface confidence (ipTM ≈ 0.40) reflects that: designing a novel binder
  from scratch is far less of a sure thing than folding a known one.

## The finding: a humanization fork, and its cost

This is the part worth explaining slowly, because it is a real antibody-engineering story
sitting in our own data.

An antibody raised in a mouse — or designed by a model — has a **framework** (the scaffold
holding the binding loops) that does not look human. Put that into a patient and their immune
system may attack the *drug itself* (anti-drug antibodies; historically the "HAMA" response).
**Humanization** rewrites those framework residues to match human germline antibodies so the
drug flies under the immune radar.

The catch: some framework residues are not just scaffolding — they subtly prop up the binding
loops (the "Vernier zone"). Change them and you can **lose a little binding**, which engineers
then fight to recover with "back-mutations." This is textbook, and it is the shape of what we
saw.

**Round 6 took one parent trastuzumab-line antibody and forked it:** a design branch kept the
sequence; a humanize branch mutated both chains toward human germline (which is why the two
Round 6 candidates have *different* sequence fingerprints). We then folded each against HER2 in
a later run:

| Line | Featured candidate | folded ipTM | humanness (OASis) | lives on |
|---|---|---|---|---|
| design    | `449d4688` (Boltz2 Evolved) | **0.79** | — | fingerprint `97f3ed9e79b8`, == Round 6 `a6d1540b` |
| humanized | `8981aae1` (Round 8 fold)   | **0.70** | **0.24** | fingerprint `e5bdaea95ef3`, == Round 6 `15bad6f9` |
| de novo   | `05f95b5a` (nanobody)       | 0.40 | — | independent, unique fingerprint |

A **~0.09 drop in predicted interface confidence (0.79 → 0.70)**, in the direction
humanization is *known* to push: you paid a little binding to buy a more human sequence. And
the humanness receipt (OASis 24th percentile) is stamped on the *other* row — back in Round 6,
candidate `15bad6f9` — because the folding run never recomputed it. **Same molecule, two
experiments, two halves of one trade-off**, reunited by the `antibody_hash` fingerprint in the
`candidate_summary` view.

## What the score names mean

- **ipTM** — "interface predicted TM-score", 0–1, from the structure predictor. It is the
  model's *confidence in how the two proteins are positioned against each other*. Rough reading:
  > 0.8 confident complex, ~0.7 moderate, ~0.4 shaky. **It is not an affinity (K<sub>d</sub>)** —
  it says "I'm this sure they dock like this", not "binds this tightly".
  It is also **assembly-wide**: for a 3-chain H/L/T fold the heavy–light interface is baked into the
  same number as the antibody–HER2 one. So it only compares cleanly between structures with the
  *same* chain composition — and it means nothing at all for a fold with no target in it (see
  `interface_kind` in the `candidate_summary` view, and the stress log entry for 2026-07-31).
- **OASis percentile (humanness)** — BioPhi slides a window along the antibody and asks how
  often each short peptide actually appears in real human antibody repertoires (the OAS
  database). Higher percentile = more human-looking = lower predicted immunogenicity risk. The
  humanized line at the 24th percentile is honestly *not very human yet* — a real observation,
  not a bug.
- **Humatch** — an ML (CNN) humanness classifier; a second opinion alongside OASis.
- **epitope residues** — the HER2 positions the model predicts are in contact with the
  antibody. The *count* is a column in the view; the *list* is what paints red on chain T in
  the viewer. The numbering matches the PDB directly because the analysis module numbered
  residues in the same Boltz2 structure it analyzed (no offset).

## Why this maps so cleanly onto the schema

The early experiments were exploratory — "bolt on a new scoring module and see what happens" —
before there was a scientific question; only the last few rounds explicitly chased humanness.
That is why one candidate carries 130 scores and the next carries 59, and why humanness and
structure are emitted on *different rows in different CSVs*. The flexible schema
(`scores` JSONB + `candidate_chains` + the `candidate_summary` view) swallowed that mess without
a migration, and the `antibody_hash` fingerprint is what lets us walk one molecule across the
exploratory sprawl.

## Caveats

1. **All predicted.** Boltz2 predicts structure; this is not a crystal structure or an SPR
   affinity measurement. "ipTM 0.79 > 0.70" means *more confident interface*, and only
   *suggests* tighter binding.
2. **n = tiny.** Single diffusion samples, a handful of candidates. A beautifully-shaped
   anecdote, not a titration curve.
3. **Direction, not magnitude.** The humanization→binding drop matches the known phenomenon
   *directionally*. Do not quote 0.09 as if it were a measured ΔΔG.
   The 0.79 vs 0.70 comparison **is** fair in one specific way that matters: both are `H/L/T` folds,
   so the same interfaces feed both numbers. The de novo nanobody's 0.40 in the table above is a
   2-chain `H/T` fold and is **not** strictly comparable to either — it is listed to show the third
   design route, not to rank it against the other two.
4. **Exploratory provenance.** Many early runs were "try a module" experiments; the messiness
   is genuine, and absorbing it is the schema's job.

## Where this could go: published ground truth

The same fingerprint-and-NULL machinery would let us fold in a *reference* trastuzumab (known
epitope, known humanness, ideally an experimental affinity) and line our predictions up against
it — "here's what the model said, here's what's published." The schema is already shaped to eat
external score-shapes; this is the natural next chapter.
