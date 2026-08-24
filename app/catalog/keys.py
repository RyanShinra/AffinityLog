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


class _Rule(NamedTuple):
    """What one heading's key strings encode beyond the heading itself.

    `variant_kind` is the VariantKind member NAME, matching what the Postgres enum stores. Still a
    `str` here; `docs/type-safety-plan.md` stage 3 is where it becomes the enum itself.

    An earlier version of this docstring justified the `str` by claiming `app/catalog/` must not
    import the ORM. There was never such a rule — `invariants.py` imported it until the enum moved
    into this package. What IS true is the direction: `app/models/orm.py` now imports
    `app.catalog.variant_kind`, so that module has to stay a leaf.
    """

    variants: frozenset[str]
    variant_kind: str


# Headings whose key strings encode a run PARAMETER rather than naming a distinct quantity.
# EvoProtGrad reports the same statistic once per protein language model and distinguishes them by
# prefixing the model name — so `esm_pseudolikelihood_ratio` and `amplify_pseudolikelihood_ratio`
# are ONE metric with two variants, not two metrics. Splitting them here is what lets a consumer ask
# for "pseudolikelihood ratio" and get both arms of the sweep.
#
# THE DATA IS THE SOURCE OF TRUTH; THE REGEX IS DERIVED FROM IT.
# This used to be the other way round — one compiled pattern per module, with the columns and
# variants trapped inside it as capture groups. Nothing outside could ask "which columns does this
# cover?", so the satisfiability check in `app/catalog/invariants.py` had to settle for the only
# thing that WAS reachable: the bare kind name. That gave it a false negative in exactly the shape
# it was written to catch — PARAMETER read as recoverable everywhere, when it is recoverable for
# precisely one heading. Keying on `(module, column_key)` and listing the variants makes the
# question answerable and the drift unrepresentable.
#
# ADDING AN ENTRY: the key is the heading AFTER splitting, i.e. what `decompose()` returns as
# `column_key`, not the raw export header. `variants` is the closed set of values the prefix may
# take — closed because these name real things (protein language models, here) that are enumerable
# from the module's own repo, not free text. Both are small and knowable; see
# docs/pr-14-diary.md and the note in `_pattern_for` on the one shape this assumes.
_VARIANT_RULES: Final[dict[tuple[str, str], _Rule]] = {
    ("evoprotgrad", "pseudolikelihood_ratio"): _Rule(
        variants=frozenset({"esm", "amplify"}),
        variant_kind="PARAMETER",
    ),
}


def _pattern_for(column_key: str, variants: frozenset[str]) -> re.Pattern[str]:
    """Build the matcher for one rule.

    ASSUMES ONE SHAPE: `{variant}_{column_key}`, a prefix and an underscore. That is the only shape
    in the corpus, and both halves are escaped so a column key containing regex metacharacters is
    matched literally. A module that encoded its variant as a suffix, or with another separator,
    would need a second shape here rather than a new entry in the table above — which is the price
    of making the data primary, and is deliberate: the corpus is the source of truth for what
    exists, so a shape we have never seen is not one to guess at.
    """
    if not variants:
        # An empty alternation compiles to `^(?P<variant>)_(?P<column>foo)$`, which happily matches
        # the literal key `module._foo` and yields variant="". That variant matches no catalog row,
        # so the key would resolve to no metric SILENTLY — the failure this whole module exists to
        # prevent, introduced by a typo in a hand-edited table. Raising here fires at import,
        # because `_MATCHERS_PER_MODULE` below is built at import: a bad rule stops the process
        # rather than shipping a matcher that quietly parses keys wrong.
        raise ValueError(
            f"_VARIANT_RULES entry for column_key={column_key!r} declares no variants. "
            f"A rule exists to name the closed set of values its prefix may take; an empty set "
            f"means the entry should be deleted rather than left to match an empty prefix."
        )

    alternation = "|".join(re.escape(variant) for variant in sorted(variants))
    return re.compile(rf"^(?P<variant>{alternation})_(?P<column>{re.escape(column_key)})$")


# Compiled once at import and grouped by module, because `decompose()` has only the module in hand
# when it needs to match — the column_key it would look the rule up by is the rule's OUTPUT.
_MATCHERS_PER_MODULE: Final[dict[str, tuple[tuple[re.Pattern[str], _Rule], ...]]] = {}
for (_module, _column_key), _rule in _VARIANT_RULES.items():
    _MATCHERS_PER_MODULE.setdefault(_module, ())
    _MATCHERS_PER_MODULE[_module] += ((_pattern_for(_column_key, _rule.variants), _rule),)


def decomposable_kinds_for(module: str, column_key: str) -> frozenset[str]:
    """The variant kinds `decompose()` can recover from a key string FOR THIS HEADING.

    Everything else — INTERFACE, and any kind a future curator invents — has to come from somewhere
    other than the key. `app/catalog/invariants.py` uses this to refuse a catalog whose heading is
    qualified along an axis nothing can supply, which would otherwise resolve to no metric silently
    for every candidate.

    Keyed by heading, not by kind. "Is PARAMETER decomposable?" is not an answerable question: the
    rules are pinned to a module AND a column, so PARAMETER is recoverable for
    `evoprotgrad.pseudolikelihood_ratio` and for nothing else in the corpus.
    """
    rule = _VARIANT_RULES.get((module, column_key))
    return frozenset({rule.variant_kind}) if rule is not None else frozenset()


def declared_variants_for(module: str, column_key: str, variant_kind: str) -> frozenset[str]:
    """Every variant this heading's key strings may carry ALONG THIS AXIS, or empty if none do.

    The companion to `decomposable_kinds_for`: that one says which axis the key encodes, this says
    which VALUES of it exist. `app/catalog/invariants.py` uses it to refuse a heading catalogued
    along a declared axis without a row for every value — seed `esm` but not `amplify` and every
    `evoprotgrad.amplify_pseudolikelihood_ratio` key resolves to nothing.

    THE AXIS IS A REQUIRED ARGUMENT, not a convenience. This took only (module, column_key) at
    first, and returned the rule's variants whatever axis the caller was asking about — so
    `evoprotgrad.pseudolikelihood_ratio` catalogued along TRANSFORM was measured against
    {esm, amplify} and refused for "having no row for" variants belonging to a different axis
    entirely, while `fastdpe.SFvCSP` with the identical shape passed. Whether a heading happens to
    carry a rule for some OTHER axis cannot be what decides it.

    That is the same defect as the one `decomposable_kinds_for` exists to fix, one axis over: a
    lookup keyed on less than the question needs will answer a question nobody asked. Taking the
    axis makes the wrong call unspellable rather than merely wrong.

    Only askable at all because the rules table carries `variants` as data. While the regex was the
    source of truth, the values were capture groups and this function could not have been written.
    """
    rule = _VARIANT_RULES.get((module, column_key))
    if rule is None or rule.variant_kind != variant_kind:
        return frozenset()
    return rule.variants


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
    for pattern, rule in _MATCHERS_PER_MODULE.get(module, ()):
        if (m := pattern.match(rest)) is not None:
            variant_kind, variant, rest = rule.variant_kind, m.group("variant"), m.group("column")
            break

    return ScoreKey(module, rest, variant_kind, variant, chain)
