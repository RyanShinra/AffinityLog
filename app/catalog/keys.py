"""Score-key anatomy: turning a raw JSONB key into the catalog identity it names.

WHY THIS LIVES IN `app/` AND NOT IN `scripts/`
----------------------------------------------
The decomposition of `boltz2.protein_iptm.H` into (module, column_key, variant_kind, variant,
chain) was born in `scripts/extract_score_keys.py`, which used it to *derive* the catalog from the
corpus. The GraphQL `ScoreEntry` resolver needs the identical decomposition in the opposite
direction: given a key, find the metric row that was seeded for it.

Two copies of that logic would be a silent-corruption bug, not a duplication nit. If the seeder and
the resolver disagreed about what `evoprotgrad.esm_pseudolikelihood_ratio.H` decomposes to, the
seeder would write a row under one identity and the resolver would look under another; the key would
stop resolving, and nothing would raise. Measured on the current corpus: 197 of 200 keys resolve on
the identity alone, so a drift here would quietly blank most of the API's meaning.

There is also a packaging reason. `pyproject.toml` has:

    [tool.setuptools.packages.find]
    include = ["app*"]

`scripts/` is not packaged, so `app` importing from `scripts` works from a source checkout and
breaks in the Docker image. The dependency has to run app <- scripts, never the reverse.

KEY ANATOMY
-----------
    module . column_key [. chain]

    temstapro.clash.H                          -> temstapro / clash            / chain H
    boltz2.protein_iptm                        -> boltz2    / protein_iptm     / no chain
    evoprotgrad.esm_pseudolikelihood_ratio.H   -> evoprotgrad / pseudolikelihood_ratio
                                                  + variant_kind=PARAMETER, variant="esm"

The chain suffix is deliberately NOT part of metric identity: `clash` is the same metric whether it
was measured on the heavy or the light chain. Chain says *which subject the value describes*, which
is why `VariantKind` has no CHAIN member. Stripping it is what collapses 200 keys to 138 metrics.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
The three-way INTERFACE disambiguation. `decompose` sees only the key, and the key does not say
which of `boltz2.protein_iptm`'s three metric rows applies — that depends on which chains went into
the fold, which is a property of the *candidate*, not of the key. Resolving it needs
`candidate_summary.interface_kind` (the CASE), so it belongs in the resolver. Keeping this module a
pure function of the key string is what makes it testable without a database.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple

# Trailing .H/.L/.T — the chain the value was measured on, not part of the metric's identity.
_CHAIN_SUFFIX: Final[re.Pattern[str]] = re.compile(r"\.(H|L|T)$")

# Columns whose name encodes a run PARAMETER rather than naming a distinct quantity. EvoProtGrad
# reports the same statistic once per protein language model and distinguishes them by prefixing
# the model name — so `esm_pseudolikelihood_ratio` and `amplify_pseudolikelihood_ratio` are ONE
# metric with two variants, not two metrics. Splitting them here is what lets a consumer ask for
# "pseudolikelihood ratio" and get both arms of the sweep.
#
# Format: module -> (regex with a `variant` and `column` group, VariantKind name)
_VARIANT_RULES: Final[dict[str, tuple[re.Pattern[str], str]]] = {
    "evoprotgrad": (
        re.compile(r"^(?P<variant>esm|amplify)_(?P<column>pseudolikelihood_ratio)$"),
        "PARAMETER",
    ),
}

# The variant kinds `decompose()` can recover from a key STRING, derived from the rules above so the
# two cannot drift. Everything else — INTERFACE, and any kind a future curator invents — has to come
# from somewhere other than the key. `app/catalog/invariants.py` uses this to refuse a catalog whose
# heading is qualified along an axis nothing can supply, which would otherwise resolve to no metric
# silently for every candidate.
DECOMPOSABLE_VARIANT_KINDS: Final[frozenset[str]] = frozenset(kind for _, kind in _VARIANT_RULES.values())

# Two keys in the corpus ("tier", "recommendation") carry NO module prefix — they are run-level
# verdicts the exporter attaches to the whole result, not a module's output. `recommendation` is
# even a paragraph of AI-generated prose. Filing them under a synthetic module keeps them in the
# skeleton (nothing silently lost) while flagging that they are not really module metrics.
_NO_MODULE: Final[str] = "_export"


class ScoreKey(NamedTuple):
    """One raw JSONB key, decomposed.

    The first four fields are the catalog's natural key — they match `metrics`'
    `UNIQUE (module_id, column_key, variant_kind, variant)`. `chain` is deliberately outside that
    identity: it says which subject the value describes, not which metric it is.
    """

    module: str
    column_key: str
    variant_kind: str | None
    variant: str | None
    chain: str | None


def decompose(key: str) -> ScoreKey:
    """Split one raw JSONB key into its catalog identity plus the chain it was measured on."""
    if "." not in key:
        return ScoreKey(_NO_MODULE, key, None, None, None)
    module, _, rest = key.partition(".")
    chain_match = _CHAIN_SUFFIX.search(rest)
    chain = chain_match.group(1) if chain_match else None
    if chain_match:
        rest = rest[: chain_match.start()]

    variant_kind: str | None = None
    variant: str | None = None
    rule = _VARIANT_RULES.get(module)
    if rule is not None:
        pattern, kind = rule
        if (m := pattern.match(rest)) is not None:
            variant_kind, variant, rest = kind, m.group("variant"), m.group("column")

    return ScoreKey(module, rest, variant_kind, variant, chain)
