"""Score-key decomposition tests — no database required.

`decompose` is the single point where a raw JSONB key becomes a catalog identity. Both the seeding
script and (soon) the GraphQL `ScoreEntry` resolver go through it, which is the whole reason it was
moved into `app/`: if the two ever disagreed about what a key means, the seeder would write a row
under one identity and the resolver would look under another, and the key would simply stop
resolving with nothing raised.

That failure is invisible — no exception, no failing query, just missing meaning — so the rules are
pinned here rather than left to the corpus to demonstrate. The example keys below are all real ones
from the loaded exports.
"""

from __future__ import annotations

import pytest

from app.catalog.identifiers import ColumnKey, ModuleName, VariantName
from app.catalog.keys import ScoreKey, _pattern_for, decompose
from app.catalog.variant_kind import VariantKind
from app.models import orm as db


def _key(
    module: str,
    column_key: str,
    variant_kind: VariantKind | None = None,
    variant: str | None = None,
    chain: db.ChainRole | None = None,
) -> ScoreKey:
    """An EXPECTED ScoreKey, built from plain strings.

    The `NewType` calls are here rather than at ~30 call sites because wrapping every literal makes
    an assertion about `boltz2.protein_iptm` harder to read than the thing it is testing.

    Laundering the types is safe in a test and would not be in production code, which is the whole
    distinction the aliases exist for: here the expected value is compared against `decompose()`'s
    real output, so a transposed argument fails the assertion immediately and loudly. In production
    a transposed identity just misses the catalog and resolves to nothing, with nothing to notice.
    """
    return ScoreKey(
        ModuleName(module),
        ColumnKey(column_key),
        variant_kind,
        VariantName(variant) if variant is not None else None,
        chain,
    )


class TestPlainKeys:
    def test_module_and_column(self) -> None:
        assert decompose("boltz2.protein_iptm") == _key("boltz2", "protein_iptm", None, None, None)

    def test_only_the_first_dot_separates_the_module(self) -> None:
        # A column name may itself contain dots; `partition` splits once, so everything after the
        # first dot is the column key.
        assert decompose("structure_analysis_boltz2.num_epitope_residues") == _key(
            "structure_analysis_boltz2", "num_epitope_residues", None, None, None
        )

    def test_a_column_key_may_contain_spaces(self) -> None:
        # JSONB keys are just strings and the CSV header goes in verbatim, so this is normal.
        assert decompose("biophi.OASis Percentile_After.H") == _key(
            "biophi", "OASis Percentile_After", None, None, db.ChainRole.HEAVY
        )


class TestIdentity:
    def test_it_is_the_first_four_fields_in_order(self) -> None:
        # Pins the ORDER, which is the whole reason the property exists. `metric_by_identity` is
        # keyed by this tuple, and a transposition typechecks, misses, and resolves to no metric
        # with no exception and no log — see docs/type-safety-plan.md.
        key = decompose("evoprotgrad.esm_pseudolikelihood_ratio.L")
        assert key.identity == ("evoprotgrad", "pseudolikelihood_ratio", VariantKind.PARAMETER, "esm")
        assert key.identity == (key.module, key.column_key, key.variant_kind, key.variant)

    def test_the_chain_is_not_in_it(self) -> None:
        # The same metric measured on two chains has ONE identity. This is what collapses the
        # corpus's 200 raw keys to 138 catalog rows.
        heavy = decompose("temstapro.clash.H")
        light = decompose("temstapro.clash.L")
        assert heavy.identity == light.identity
        assert heavy.chain != light.chain


class TestChainSuffix:
    def test_the_chain_is_captured_and_stripped(self) -> None:
        assert decompose("temstapro.clash.H") == _key("temstapro", "clash", None, None, db.ChainRole.HEAVY)

    def test_chain_is_not_part_of_identity(self) -> None:
        # THE POINT OF THE WHOLE MODULE. `clash` is the same metric whether measured on the heavy or
        # the light chain — which is what collapses 200 raw keys to 138 identities. Chain says which
        # subject the value describes, not which metric it is, and that is why VariantKind has no
        # CHAIN member.
        heavy = decompose("temstapro.clash.H")
        light = decompose("temstapro.clash.L")
        assert heavy[:4] == light[:4]
        assert (heavy.chain, light.chain) == (db.ChainRole.HEAVY, db.ChainRole.LIGHT)

    def test_only_H_L_T_count_as_a_chain_suffix(self) -> None:
        # A trailing segment that is not a chain label stays part of the column key. Stripping it
        # would silently merge two different metrics into one identity.
        assert decompose("module.some_column.X") == _key("module", "some_column.X", None, None, None)

    def test_the_suffix_must_be_at_the_end(self) -> None:
        assert decompose("module.H_bond_count") == _key("module", "H_bond_count", None, None, None)


class TestARuleMustDeclareItsVariants:
    """An empty `variants` set is a typo, and it used to compile into a working-looking matcher.

    `"|".join(())` is `""`, so the pattern became `^(?P<variant>)_(?P<column>foo)$` — which matches
    the key `module._foo` and returns variant="". No catalog row has an empty-string variant, so
    such a key would resolve to no metric silently, which is the exact failure this module exists
    to prevent. It now raises at import instead, because the matcher table is built at import.
    """

    def test_an_empty_variant_set_is_refused(self) -> None:
        with pytest.raises(ValueError, match="declares no variants"):
            _pattern_for(ColumnKey("pseudolikelihood_ratio"), frozenset())

    def test_a_bare_underscore_is_not_a_variant(self) -> None:
        """What the guard prevents, shown with a real rule: the prefix must actually be there."""
        pattern = _pattern_for(ColumnKey("pseudolikelihood_ratio"), frozenset({VariantName("esm")}))

        assert pattern.match("_pseudolikelihood_ratio") is None
        assert pattern.match("esm_pseudolikelihood_ratio") is not None


class TestVariantRules:
    """EvoProtGrad prefixes one statistic with the model name — two variants, not two metrics."""

    def test_esm_prefix_becomes_a_parameter_variant(self) -> None:
        assert decompose("evoprotgrad.esm_pseudolikelihood_ratio") == _key(
            "evoprotgrad", "pseudolikelihood_ratio", VariantKind.PARAMETER, "esm", None
        )

    def test_amplify_prefix_becomes_the_other_variant(self) -> None:
        assert decompose("evoprotgrad.amplify_pseudolikelihood_ratio") == _key(
            "evoprotgrad", "pseudolikelihood_ratio", VariantKind.PARAMETER, "amplify", None
        )

    def test_both_arms_share_one_column_key(self) -> None:
        # This is what lets a consumer ask for "pseudolikelihood ratio" and get both arms of the
        # sweep, instead of having to know the model names.
        esm = decompose("evoprotgrad.esm_pseudolikelihood_ratio.H")
        amplify = decompose("evoprotgrad.amplify_pseudolikelihood_ratio.H")
        assert esm.column_key == amplify.column_key == "pseudolikelihood_ratio"
        assert {esm.variant, amplify.variant} == {"esm", "amplify"}

    def test_the_variant_rule_applies_with_a_chain_suffix_too(self) -> None:
        # The chain is stripped BEFORE the variant rule runs, so the rule's anchored regex still
        # matches. Order matters here and this is what pins it.
        assert decompose("evoprotgrad.esm_pseudolikelihood_ratio.L") == _key(
            "evoprotgrad", "pseudolikelihood_ratio", VariantKind.PARAMETER, "esm", db.ChainRole.LIGHT
        )

    def test_the_rule_is_scoped_to_its_module(self) -> None:
        # A column starting with "esm_" under any other module is left completely alone — the rule
        # is keyed by module, not applied globally.
        assert decompose("othermodule.esm_pseudolikelihood_ratio") == _key(
            "othermodule", "esm_pseudolikelihood_ratio", None, None, None
        )

    def test_an_unmatched_column_in_a_ruled_module_is_untouched(self) -> None:
        assert decompose("evoprotgrad.sampling_count") == _key("evoprotgrad", "sampling_count", None, None, None)


class TestPrefixlessKeys:
    """`tier` and `recommendation` carry no module — run-level verdicts, not module outputs."""

    def test_a_key_with_no_dot_goes_under_the_synthetic_module(self) -> None:
        assert decompose("tier") == _key("_export", "tier", None, None, None)

    def test_recommendation_too(self) -> None:
        # AI-generated prose rather than a measurement. Filed rather than dropped, so nothing is
        # silently lost, but the `_export` module name flags that it is not really a metric.
        assert decompose("recommendation") == _key("_export", "recommendation", None, None, None)
