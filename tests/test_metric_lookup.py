"""The vocabulary of the two-tier lookup, and the one method that performs it.

WRITTEN BEFORE THE CODE. This file is the specification for `Heading`, `MetricIdentity`,
`VariantAxes` and `MetricCatalog.metric_for` — it is expected to fail at import until those exist.

WHY THESE TYPES EXIST AT ALL, since none of them adds behavior the tuples lacked:

    Mapping[tuple[ModuleName, ColumnKey], frozenset[VariantKind]]

is two inferred types mapped to each other, and reading it costs about ten seconds — you have to
hold "module plus column, which names a column uniquely across every module" while working out that
the frozenset is an answer ABOUT that heading rather than something you index into. `Heading` and
`VariantAxes` name both halves, and the membership test that used to read

    if VariantKind.INTERFACE in kinds:      # an interface in kinds? which interface?

becomes `if axes.interface_qualified:`, which says what is actually being asked. `MetricIdentity`
was a bare tuple alias for the same reason and is promoted to a class so that building one is a
named construction rather than four values in an order you have to remember.

This is deliberately not idiomatic Python. It buys the reader a second of comprehension per
encounter, at the cost of three declarations, in the one place in the project where a silent wrong
answer is most expensive.
"""

from __future__ import annotations

import pytest
from graphql import GraphQLError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identifiers import ColumnKey, ModuleName, VariantName
from app.catalog.interface_kind import InterfaceKind
from app.catalog.keys import Heading, MetricIdentity, VariantAxes, decompose
from app.catalog.variant_kind import VariantKind
from app.graphql.context import Context


class TestTheNamedTuples:
    """`Heading` and `MetricIdentity` — what a metric is called, before and after disambiguation."""

    def test_a_score_key_yields_its_own_heading(self) -> None:
        """Nobody rebuilds the pair by hand; `ScoreKey` already holds both halves."""
        assert decompose("boltz2.protein_iptm").heading == Heading(ModuleName("boltz2"), ColumnKey("protein_iptm"))

    def test_the_heading_survives_a_chain_suffix(self) -> None:
        """`.H` says which chain the value describes, not which metric it is."""
        assert decompose("temstapro.clash.H").heading == decompose("temstapro.clash.L").heading

    def test_an_identity_names_its_four_fields(self) -> None:
        """The point of the promotion: `identity.variant_kind`, not `identity[2]`.

        Order still matters — it is the key into `metric_by_identity` — but a reader no longer has
        to know it, and `tests/test_extract_score_keys.py` pins the arity against
        `scripts/extract_score_keys.py`'s parallel definition.
        """
        identity = decompose("evoprotgrad.esm_pseudolikelihood_ratio.H").identity

        assert identity.module == "evoprotgrad"
        assert identity.column_key == "pseudolikelihood_ratio"
        assert identity.variant_kind is VariantKind.PARAMETER
        assert identity.variant == "esm"

    def test_for_interface_builds_the_identity_the_catalog_stores(self) -> None:
        """`metrics.variant` holds the view's CASE STRING, so the enum's `.value` is what goes in.

        This is the boundary the whole design turns on and it is one line, so it gets its own test:
        `InterfaceKind.ANTIBODY_TARGET_COMPLEX` is a member, and the catalog row it names is keyed
        by the text 'antibody-target complex'. Passing the member, or its `.name`, would build an
        identity in no catalog and resolve to nothing — silently.
        """
        heading = Heading(ModuleName("boltz2"), ColumnKey("protein_iptm"))

        identity = MetricIdentity.for_interface(heading, InterfaceKind.ANTIBODY_TARGET_COMPLEX)

        assert identity == MetricIdentity(
            ModuleName("boltz2"),
            ColumnKey("protein_iptm"),
            VariantKind.INTERFACE,
            VariantName("antibody-target complex"),
        )


class TestVariantAxes:
    """The set of axes along which a heading's metrics vary — usually empty."""

    def test_none_is_the_common_case(self) -> None:
        """140 of the corpus's 144 catalog rows have no variant at all."""
        assert not VariantAxes.none().interface_qualified
        assert VariantAxes.none().kinds == frozenset()

    def test_interface_qualified_is_true_only_for_interface(self) -> None:
        assert VariantAxes(frozenset({VariantKind.INTERFACE})).interface_qualified

    def test_a_parameter_axis_is_not_interface_qualified(self) -> None:
        """The asymmetry, as a boolean.

        A PARAMETER variant is spelled in the key string and `decompose()` recovers it unaided, so
        the key is already complete. An INTERFACE variant is not in the key at ALL — which is what
        `interface_qualified` means: the key cannot identify the metric on its own.
        """
        assert not VariantAxes(frozenset({VariantKind.PARAMETER})).interface_qualified


class TestMetricForResolvesOneKeyPerCandidate:
    """`MetricCatalog.metric_for` — the two-tier lookup, and the only place it is written down."""

    async def test_one_key_resolves_three_ways(self, seeded_catalog: AsyncSession) -> None:
        """The 2026-07-31 finding as a single assertion.

        One JSONB key, three interface kinds, three different metrics. Sorting on the raw value
        would rank these backwards — the meaningless ones score highest — which is why what the
        number MEANS has to come from here rather than from the key.
        """
        catalog = await Context(session=seeded_catalog).catalog()
        score_key = decompose("boltz2.protein_iptm")

        display_names = {
            kind: metric.display_name
            for kind in InterfaceKind
            if kind is not InterfaceKind.NO_CHAINS_RECORDED and (metric := catalog.metric_for(score_key, kind)) is not None
        }

        assert display_names == {
            InterfaceKind.ANTIBODY_TARGET_COMPLEX: "HER2 binding confidence",
            InterfaceKind.ANTIBODY_ONLY_HL_PAIRING: "Heavy-light pairing confidence",
            InterfaceKind.SINGLE_CHAIN_NO_INTERFACE: "Not an interface",
        }

    async def test_a_candidate_with_no_chains_resolves_to_no_metric(self, seeded_catalog: AsyncSession) -> None:
        """The documented asymmetry: three INTERFACE rows per heading, four InterfaceKind members.

        `InterfaceKind.scoreable()` excludes NO_CHAINS_RECORDED deliberately — a candidate with no
        chain rows has no interface for ipTM to describe, so a catalog row for it would be dead
        data. The consequence is that such a candidate's key resolves to nothing, which is one of
        the reasons `ScoreEntry.metric` is nullable rather than an error.
        """
        catalog = await Context(session=seeded_catalog).catalog()

        metric = catalog.metric_for(decompose("boltz2.protein_iptm"), InterfaceKind.NO_CHAINS_RECORDED)

        assert metric is None

    async def test_an_interface_key_without_a_candidates_kind_is_refused(self, seeded_catalog: AsyncSession) -> None:
        """Not a `None` return, and emphatically not an `assert`.

        The identity this would otherwise build — (module, column, INTERFACE, None) — is in no
        catalog, so the key would resolve to nothing and the score would silently lose its meaning.
        That is the precise failure the two-tier design exists to prevent, so it must not be
        expressible: `python -O` strips asserts, and returning None would make an unresolvable key
        indistinguishable from an uncataloged one.
        """
        catalog = await Context(session=seeded_catalog).catalog()

        with pytest.raises(GraphQLError) as raised:
            catalog.metric_for(decompose("boltz2.protein_iptm"), interface_kind=None)

        assert (raised.value.extensions or {})["code"] == "INTERFACE_KIND_REQUIRED"

    async def test_a_parameter_key_needs_no_candidate(self, seeded_catalog: AsyncSession) -> None:
        """197 of the corpus's 200 keys never reach tier two; this is why."""
        catalog = await Context(session=seeded_catalog).catalog()

        metric = catalog.metric_for(decompose("evoprotgrad.esm_pseudolikelihood_ratio.H"), interface_kind=None)

        assert metric is not None
        assert metric.variant == "esm"

    async def test_an_uncatalogued_key_resolves_to_none(self, seeded_catalog: AsyncSession) -> None:
        """A miss is information, not an error: it says the column did not come from Bio Discovery.

        Distinct from the raise above, which says the catalog HAS rows for this heading and we were
        not given enough to choose between them.
        """
        catalog = await Context(session=seeded_catalog).catalog()

        assert catalog.metric_for(decompose("nosuchmodule.nosuchcolumn"), interface_kind=None) is None

    async def test_the_chain_suffix_does_not_change_the_metric(self, seeded_catalog: AsyncSession) -> None:
        """Heavy and light are the same metric measured on two subjects — one catalog row, not two.

        This is the 200-keys-to-144-rows collapse, asserted at the lookup rather than in the script
        that derived it.
        """
        catalog = await Context(session=seeded_catalog).catalog()

        heavy = catalog.metric_for(decompose("temstapro.clash.H"), interface_kind=None)
        light = catalog.metric_for(decompose("temstapro.clash.L"), interface_kind=None)

        assert heavy is not None
        assert heavy is light
