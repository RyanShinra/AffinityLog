"""Score-key anatomy: turning a raw JSONB key into the catalog identity it names.

PLACEHOLDER — nothing has moved yet. This file exists so the move can be reviewed before it
happens. See "WHAT MOVES HERE" below.

WHY THIS FILE NEEDS TO EXIST AT ALL
-----------------------------------
The decomposition of `boltz2.protein_iptm.H` into (module, column_key, variant_kind, variant,
chain) currently lives in `scripts/extract_score_keys.py`, which is where it was born — it was
written to *derive* the catalog from the corpus. But the GraphQL `ScoreEntry` resolver has to
perform the identical decomposition in the opposite direction: given a key, find the metric row
that was seeded for it.

Two copies of that logic would be a silent-corruption bug, not a duplication nit. If the seeder
and the resolver disagree about what `evoprotgrad.esm_pseudolikelihood_ratio.H` decomposes to,
the seeder writes a row under one identity and the resolver looks under another; the key stops
resolving, and nothing raises. Measured on the current corpus: 197 of 200 keys resolve on the
identity alone, so a drift here would quietly blank most of the API's meaning.

There is also a packaging reason. `pyproject.toml` has:

    [tool.setuptools.packages.find]
    include = ["app*"]

`scripts/` is not packaged. `app` importing from `scripts` works from a source checkout and
breaks in the Docker image — the direction of the dependency has to be app <- scripts, never the
reverse.

WHAT MOVES HERE (verbatim, from scripts/extract_score_keys.py)
--------------------------------------------------------------
    _CHAIN_SUFFIX   the trailing .H/.L/.T regex
    _VARIANT_RULES  the evoprotgrad esm_/amplify_ split -> VariantKind.PARAMETER
    _NO_MODULE      the "_export" synthetic module for the two prefix-less keys
    decompose()     the function itself, unchanged

`extract_score_keys.py` keeps `MetricKey`, `collect()`, and `main()`, and imports the four names
above from here. Pure relocation — no behaviour change, so the existing seeded catalog stays
valid and the scripts stay idempotent.

WHAT DOES *NOT* MOVE HERE
-------------------------
The three-way INTERFACE disambiguation. `decompose()` sees only the key, and the key does not
say which of `boltz2.protein_iptm`'s three metric rows applies — that depends on which chains
went into the fold, which is a property of the *candidate*, not of the key. Resolving that needs
`candidate_summary.interface_kind` (the CASE), so it belongs in the resolver, not here. Keeping
this module a pure function of the key string is what makes it testable without a database.
"""

from __future__ import annotations

# TODO: move _CHAIN_SUFFIX, _VARIANT_RULES, _NO_MODULE and decompose() here from
# scripts/extract_score_keys.py, then have that script import them from this module.
