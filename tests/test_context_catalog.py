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

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.catalog.keys import MetricIdentity, decompose
from app.catalog.variant_kind import VariantKind
from app.database import AsyncSessionLocal
from app.graphql.context import Context
from app.graphql.schema import schema
from app.models import orm as db


def _identity_for(key: str, catalog_kinds: frozenset[str], interface_kind: str | None) -> MetricIdentity:
    """The two-tier lookup, written out longhand.

    This mirrors what the ScoreEntry resolver will do. It lives in the test for now because the
    resolver does not exist yet — when it does, this should be deleted and the test should call it
    instead, or the test stops guarding the real code path.
    """
    score_key = decompose(key)
    if VariantKind.INTERFACE.name in catalog_kinds:
        if interface_kind is None:
            # Not an `assert`: `python -O` strips those, and the identity this would build
            # instead — (module, column, 'INTERFACE', None) — is in no catalog, so the key would
            # resolve to nothing silently. That is the precise failure the two-tier design exists
            # to prevent, so it must not be removable by an interpreter flag.
            raise ValueError(f"{key!r} is interface-qualified; resolving it needs the candidate's interface kind")
        return (score_key.module, score_key.column_key, VariantKind.INTERFACE.name, interface_kind)
    return score_key.identity


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
        assert VariantKind.INTERFACE.name in catalog.variant_kinds_per_heading[("boltz2", "protein_iptm")]

    async def test_a_heading_can_carry_several_variants_of_one_kind(self, seeded_catalog: AsyncSession) -> None:
        catalog = await Context(session=seeded_catalog).catalog()

        # Two PARAMETER rows, one kind. The frozenset is about AXES, not about how many rows exist.
        assert catalog.variant_kinds_per_heading[("evoprotgrad", "pseudolikelihood_ratio")] == frozenset({"PARAMETER"})
        assert len([i for i in catalog.metric_by_identity if i[:2] == ("evoprotgrad", "pseudolikelihood_ratio")]) == 2

    async def test_relationships_are_eager_loaded(self, seeded_catalog: AsyncSession) -> None:
        """Touching these after the query must not raise MissingGreenlet.

        `expunge_all()` is what gives this test teeth, and without it the test was worthless.
        The fixture creates the Modules and Concepts in this same session, so they sit in its
        identity map — and a lazy load that can be answered from the identity map short-circuits
        with no IO and no greenlet. Measured: with `selectinload(module)` and `selectinload(concept)`
        deleted from `_load_catalog`, the whole file still passed.

        Expunging models what a real request looks like. `get_session` hands each request a fresh
        session with nothing cached, so `metric.module.name` is a genuine round trip — which is
        exactly the round trip that cannot happen outside the greenlet. With the expunge in place,
        removing either eager load raises MissingGreenlet here.
        """
        seeded_catalog.expunge_all()

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

        assert bare.identity not in catalog.metric_by_identity

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
    the one per-request session in the same tick. Both of these failed before the session grew a
    lock; neither would have been caught by any other test in the suite.
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

        The session is built from the engine here rather than taken from the `session` fixture,
        because that fixture's session is already warm — and warm is exactly the state where this
        does NOT reproduce. It deliberately does not use `AsyncSessionLocal` either: that is
        unbound outside a `session`-requesting test, and binding it would defeat the point. What
        this needs is simply a session that has not connected yet, which is what production's
        `get_session` hands every request.

        WHICH MEANS THIS SESSION IS UNMANAGED, and that is a real cost rather than a detail. It
        sits outside the `session` fixture's transaction, so there is nothing to roll it back:
        anything written here would persist in the test database for every later test in the run.
        It is read-only today and must stay that way. The protection cannot simply be added either
        — opening a transaction to roll back would check out a connection, and a session with a
        connection is warm, which is the one state where the bug does not reproduce. The test needs
        a virgin session, and a virgin session is by definition one nothing is managing yet.

        Before the lock: `IllegalStateChangeError: Method 'close()' can't be called here` raised out
        of the session's own `__aexit__`, so the request 500s with a poisoned session rather than
        returning a GraphQL error.
        """
        virgin_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with virgin_maker() as virgin_session:
            result = await schema.execute(
                "{ candidates { sequenceId } experiments { name } }",
                context_value=Context(session=virgin_session),
            )

        assert result.errors is None, f"expected no errors, got {result.errors}"


class TestTheFixtureContainsWhatATestWrites:
    """Regression test for the leak the max-effort review found.

    Before the fix the `engine` fixture bound `AsyncSessionLocal` to the ENGINE, so a seeder-style
    `commit()` opened its own connection and landed OUTSIDE the `session` fixture's transaction:
    permanent, and visible to every later test. It is now bound to the test's own connection, so
    the same commit is a SAVEPOINT release inside that transaction and dies with it.

    ONE TEST, DELIBERATELY. This was two — a writer with no assertions at all, and a reader that
    checked the count was zero — which meant `pytest -k test_b_sees_none_of_it` or `pytest --lf`
    ran the reader alone against an empty schema and passed having proved nothing. Verified: both
    halves passed in isolation. A regression test that is only valid when the whole file runs in
    order is a regression test that reports green in exactly the situation you reach for it.

    The two assertions below are what the pair was trying to say, and each needs the other. Visible
    inside proves the write actually happened, so absence outside cannot be explained by nothing
    having been written. Invisible outside proves it did not escape.
    """

    async def test_a_seeders_commit_is_contained_by_the_fixture(self, session: AsyncSession, engine: AsyncEngine) -> None:
        probe = "leak-probe"

        # Exactly what a seeder does: AsyncSessionLocal, add, commit.
        async with AsyncSessionLocal() as app_session:
            app_session.add(db.Module(name=probe, module_type=db.ModuleType.SCORE, functions=[]))
            await app_session.commit()

        count = select(func.count()).select_from(db.Module).where(db.Module.name == probe)

        inside = await session.scalar(count)
        assert inside == 1, "the commit did not reach the fixture's transaction at all"

        # A genuinely separate connection. The fixture's outer transaction is still open, so a
        # contained write is invisible here while an escaped one — a real COMMIT on its own
        # connection — would be visible to everybody. That difference is the whole test.
        async with engine.connect() as outside:
            escaped = await outside.scalar(count)

        assert escaped == 0, "a commit through AsyncSessionLocal escaped the fixture's rollback"


class TestTheTwoLocksAreSeparate:
    """The memo lock and the session lock must be different objects.

    `catalog()` holds `_catalog_lock` across the whole of `_load_catalog()`, and `_load_catalog()`
    issues a statement through `execute_statement()`, which takes the session's lock. That is only
    safe while the two are distinct. Collapse them into one and the task waits on a lock it already
    holds — `asyncio.Lock` is not reentrant — so the request hangs forever with no exception and no
    traceback, while every other request on the loop is served normally.

    These are timeout-bounded on purpose. A deadlock does not fail a test, it *hangs* one, and a
    hung suite reads as CI being slow rather than as a bug.
    """

    async def test_the_memo_lock_is_not_the_sessions_lock(self, seeded_catalog: AsyncSession) -> None:
        context = Context(session=seeded_catalog)

        assert context._catalog_lock is not context._session._lock

    async def test_the_catalog_builds_through_the_ordinary_front_door(self, seeded_catalog: AsyncSession) -> None:
        """`_load_catalog` calls `execute_statement()` like every resolver does, holding the memo lock."""
        context = Context(session=seeded_catalog)

        catalog = await asyncio.wait_for(context.catalog(), timeout=10.0)

        assert catalog.metric_by_identity, "the seeded catalog is not empty"

    async def test_collapsing_them_into_one_lock_deadlocks(self, seeded_catalog: AsyncSession) -> None:
        """Break the fix on purpose, and watch it hang.

        This is the pre-cleanup design in one line: point the memo lock at the session's lock, so
        both invariants are served by one object again. Without it, nothing in the suite would
        notice if the two locks were quietly merged back together — every other test would still
        pass, because they pass under both designs.
        """
        context = Context(session=seeded_catalog)
        context._catalog_lock = context._session._lock

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(context.catalog(), timeout=1.0)
