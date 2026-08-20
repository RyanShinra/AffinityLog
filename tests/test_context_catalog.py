"""`Context.catalog()` against a real, migrated Postgres.

The test that matters here is `test_the_same_key_means_different_things_per_candidate`. Everything
else checks plumbing; that one checks the finding the whole schema exists for — that
`boltz2.protein_iptm` is three different physical quantities depending on which chains were folded,
and that the API resolves each to the right one. Break it and the API starts reporting an
antibody's heavy-light pairing confidence as if it were HER2 binding, silently, with the
meaningless values ranking highest.

See docs/schema-stress-log.md (2026-07-31) for the discovery, and app/graphql/context.py for how
the two-tier lookup carries it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.catalog.keys import decompose
from app.database import AsyncSessionLocal
from app.graphql.context import Context, MetricIdentity
from app.graphql.schema import schema
from app.models import orm as db


def _identity_for(key: str, catalog_kinds: frozenset[str], interface_kind: str | None) -> MetricIdentity:
    """The two-tier lookup, written out longhand.

    This mirrors what the ScoreEntry resolver will do. It lives in the test for now because the
    resolver does not exist yet — when it does, this should be deleted and the test should call it
    instead, or the test stops guarding the real code path.
    """
    score_key = decompose(key)
    if db.VariantKind.INTERFACE.name in catalog_kinds:
        assert interface_kind is not None, "an interface-qualified key needs the candidate's kind"
        return (score_key.module, score_key.column_key, db.VariantKind.INTERFACE.name, interface_kind)
    return (score_key.module, score_key.column_key, score_key.variant_kind, score_key.variant)


class TestCatalogIsBuiltFromTheDatabase:
    async def test_both_indexes_are_built_in_one_pass(self, seeded_catalog: AsyncSession) -> None:
        catalog = await Context(session=seeded_catalog).catalog()

        assert len(catalog.metric_by_identity) == 6, "six metric rows in, six identities out — none collided"
        assert set(catalog.variant_kinds_per_heading) == {
            ("boltz2", "protein_iptm"),
            ("evoprotgrad", "pseudolikelihood_ratio"),
        }, "only headings with a non-NULL variant_kind appear; temstapro.clash is absent"

    async def test_variant_kinds_are_member_names_not_values(self, seeded_catalog: AsyncSession) -> None:
        catalog = await Context(session=seeded_catalog).catalog()

        assert catalog.variant_kinds_per_heading[("boltz2", "protein_iptm")] == frozenset({"INTERFACE"})
        # `.value` would give "interface" and match nothing decompose() produces — silently.
        assert db.VariantKind.INTERFACE.name in catalog.variant_kinds_per_heading[("boltz2", "protein_iptm")]

    async def test_a_heading_can_carry_several_variants_of_one_kind(self, seeded_catalog: AsyncSession) -> None:
        catalog = await Context(session=seeded_catalog).catalog()

        # Two PARAMETER rows, one kind. The frozenset is about AXES, not about how many rows exist.
        assert catalog.variant_kinds_per_heading[("evoprotgrad", "pseudolikelihood_ratio")] == frozenset({"PARAMETER"})
        assert len([i for i in catalog.metric_by_identity if i[:2] == ("evoprotgrad", "pseudolikelihood_ratio")]) == 2

    async def test_relationships_are_eager_loaded(self, seeded_catalog: AsyncSession) -> None:
        """Touching these after the query must not raise MissingGreenlet."""
        catalog = await Context(session=seeded_catalog).catalog()
        metric = catalog.metric_by_identity[("boltz2", "protein_iptm", "INTERFACE", "antibody-target complex")]

        assert metric.module.name == "boltz2"
        assert metric.concept is not None and metric.concept.name == "interface_confidence"
        assert metric.benchmark_results == []
        assert metric.transform_of is None

    async def test_it_is_memoized_per_context(self, seeded_catalog: AsyncSession) -> None:
        context = Context(session=seeded_catalog)

        assert await context.catalog() is await context.catalog(), "same object, so the second call ran no query"

    async def test_an_unseeded_database_yields_an_empty_catalog(self, session: AsyncSession) -> None:
        """Empty is a legitimate state, not an error — which is why the memo sentinel is None."""
        catalog = await Context(session=session).catalog()

        assert catalog.metric_by_identity == {}
        assert catalog.variant_kinds_per_heading == {}


class TestTheIptmFinding:
    async def test_the_same_key_means_different_things_per_candidate(self, seeded_catalog: AsyncSession) -> None:
        """One JSONB key, three candidates, three different metrics.

        `boltz2.protein_iptm` decomposes to an identity that does not exist in the catalog. Which of
        the three rows applies depends on the candidate's chain composition, which the key cannot
        say.
        """
        catalog = await Context(session=seeded_catalog).catalog()
        kinds = catalog.variant_kinds_per_heading[("boltz2", "protein_iptm")]

        expected = {
            "antibody-target complex": "HER2 binding confidence",
            "antibody only (H/L pairing)": "Heavy-light pairing confidence",
            "single chain (no interface)": "Not an interface",
        }
        for interface_kind, display_name in expected.items():
            identity = _identity_for("boltz2.protein_iptm", kinds, interface_kind)
            assert catalog.metric_by_identity[identity].display_name == display_name

    async def test_the_bare_identity_resolves_to_nothing(self, seeded_catalog: AsyncSession) -> None:
        """Tier one is not an optimisation: skip it and the key resolves to no metric at all."""
        catalog = await Context(session=seeded_catalog).catalog()
        bare = decompose("boltz2.protein_iptm")

        assert (bare.module, bare.column_key, bare.variant_kind, bare.variant) not in catalog.metric_by_identity

    async def test_a_parameter_key_needs_no_second_tier(self, seeded_catalog: AsyncSession) -> None:
        """The asymmetry: a PARAMETER variant is in the key string, an INTERFACE variant is not."""
        catalog = await Context(session=seeded_catalog).catalog()
        kinds = catalog.variant_kinds_per_heading[("evoprotgrad", "pseudolikelihood_ratio")]

        identity = _identity_for("evoprotgrad.esm_pseudolikelihood_ratio.H", kinds, interface_kind=None)
        assert identity == ("evoprotgrad", "pseudolikelihood_ratio", "PARAMETER", "esm")
        assert identity in catalog.metric_by_identity

    async def test_an_ordinary_key_resolves_on_identity_alone(self, seeded_catalog: AsyncSession) -> None:
        catalog = await Context(session=seeded_catalog).catalog()
        kinds = catalog.variant_kinds_per_heading.get(("temstapro", "clash"), frozenset())

        assert kinds == frozenset(), "no entry at all, which is the common case"
        identity = _identity_for("temstapro.clash.H", kinds, interface_kind=None)
        assert identity == ("temstapro", "clash", None, None)
        assert catalog.metric_by_identity[identity].column_key == "clash"


class TestTheViewSuppliesTierTwo:
    async def test_chain_composition_determines_interface_kind(self, seeded_catalog: AsyncSession) -> None:
        """The CASE in sql/candidate_summary.sql, exercised against real rows.

        This is what `Context.interface_kinds()` will read. Asserting it here means the next chapter
        starts from a known-good view rather than trusting it.
        """
        from sqlalchemy import select

        from app.models.views import CandidateSummary

        rows = (await seeded_catalog.scalars(select(CandidateSummary))).all()
        by_candidate = {row.candidate_id: row.interface_kind for row in rows}

        assert by_candidate == {
            "complex-cand": "antibody-target complex",
            "pairing-cand": "antibody only (H/L pairing)",
            "lone-cand": "single chain (no interface)",
        }


class TestTheSessionIsNotUsedConcurrently:
    """Regression tests for the concurrency bug the max-effort review found.

    graphql-core executes sibling fields and list items with `gather`, so several resolvers reach
    the one per-request session in the same tick. Both of these failed before `Context` grew
    `_session_lock`; neither would have been caught by any other test in the suite.
    """

    async def test_the_memo_survives_concurrent_callers(self, seeded_catalog: AsyncSession) -> None:
        """Fourteen concurrent callers, one catalog.

        Fourteen is not arbitrary: it is the corpus's candidate count, and `{ candidates { scores } }`
        gathers the `scores` resolver across that list. Before the lock this produced 14 distinct
        MetricCatalog objects and 14 queries — the memo's entire purpose, silently void.
        """
        context = Context(session=seeded_catalog)

        catalogs = await asyncio.gather(*(context.catalog() for _ in range(14)))

        assert len({id(c) for c in catalogs}) == 1, "every caller must get the same catalog object"

    async def test_two_root_fields_do_not_break_a_virgin_session(self, engine: AsyncEngine) -> None:
        """The narrowest query that reproduced the original crash.

        Two root fields are gathered onto a session that has not yet checked out a connection.
        `AsyncSessionLocal` — rather than the `session` fixture — because the fixture's session is
        already warm, and warm is exactly the state where this does NOT reproduce.

        Before the lock: `IllegalStateChangeError: Method 'close()' can't be called here` raised out
        of the session's own `__aexit__`, so the request 500s with a poisoned session rather than
        returning a GraphQL error.
        """
        async with AsyncSessionLocal() as virgin_session:
            result = await schema.execute(
                "{ candidates { sequenceId } experiments { name } }",
                context_value=Context(session=virgin_session),
            )

        assert result.errors is None, f"expected no errors, got {result.errors}"
