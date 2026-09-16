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
from graphql import GraphQLError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.catalog.identifiers import ColumnKey, ModuleName, VariantName
from app.catalog.interface_kind import InterfaceKind
from app.catalog.keys import Heading, MetricIdentity, VariantAxes, decompose
from app.catalog.variant_kind import VariantKind
from app.database import AsyncSessionLocal
from app.graphql.context import Context
from app.graphql.errors import should_mask_error
from app.graphql.schema import schema
from app.models import orm as db


def _heading(module: str, column_key: str) -> Heading:
    """A heading from plain strings — see `_key` in test_catalog_keys.py for why this is safe
    in a test and would not be in production code."""
    return Heading(ModuleName(module), ColumnKey(column_key))


class TestCatalogIsBuiltFromTheDatabase:
    async def test_both_indexes_are_built_in_one_pass(self, seeded_catalog: AsyncSession) -> None:
        catalog = await Context(session=seeded_catalog).catalog()

        assert len(catalog.metric_by_identity) == 6, "six metric rows in, six identities out — none collided"
        assert set(catalog.variant_axes_per_heading) == {
            ("boltz2", "protein_iptm"),
            ("evoprotgrad", "pseudolikelihood_ratio"),
        }, "only headings with a non-NULL variant_kind appear; temstapro.clash is absent"

    async def test_variant_kinds_are_enum_members(self, seeded_catalog: AsyncSession) -> None:
        # This used to assert they were member NAMES rather than values, because `.value` would
        # give "interface", match nothing `decompose()` produced, and fail silently. Tier one holds
        # members now, so that mistake is unspellable here. The name/value boundary still exists —
        # it is `VariantKind[label]` in `app/catalog/invariants.py`, where the SQL's `::text` comes
        # back — but it is one line, and it raises rather than mismatching.
        catalog = await Context(session=seeded_catalog).catalog()

        assert catalog.variant_axes_per_heading[_heading("boltz2", "protein_iptm")] == VariantAxes(
            frozenset({VariantKind.INTERFACE})
        )

    async def test_a_heading_can_carry_several_variants_of_one_kind(self, seeded_catalog: AsyncSession) -> None:
        catalog = await Context(session=seeded_catalog).catalog()

        # Two PARAMETER rows, one kind. The frozenset is about AXES, not about how many rows exist.
        assert catalog.variant_axes_per_heading[_heading("evoprotgrad", "pseudolikelihood_ratio")] == VariantAxes(
            frozenset({VariantKind.PARAMETER})
        )
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
        metric = catalog.metric_by_identity[
            MetricIdentity.for_interface(_heading("boltz2", "protein_iptm"), InterfaceKind.ANTIBODY_TARGET_COMPLEX)
        ]

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
        assert catalog.variant_axes_per_heading == {}


class TestTheIptmFinding:
    async def test_the_same_key_means_different_things_per_candidate(self, seeded_catalog: AsyncSession) -> None:
        """One JSONB key, three candidates, three different metrics.

        `boltz2.protein_iptm` decomposes to an identity that does not exist in the catalog. Which of
        the three rows applies depends on the candidate's chain composition, which the key cannot
        say.
        """
        catalog = await Context(session=seeded_catalog).catalog()
        score_key = decompose("boltz2.protein_iptm")

        expected = {
            InterfaceKind.ANTIBODY_TARGET_COMPLEX: "HER2 binding confidence",
            InterfaceKind.ANTIBODY_ONLY_HL_PAIRING: "Heavy-light pairing confidence",
            InterfaceKind.SINGLE_CHAIN_NO_INTERFACE: "Not an interface",
        }
        for interface_kind, display_name in expected.items():
            metric = catalog.metric_for(score_key, interface_kind)

            assert metric is not None, f"{interface_kind} must resolve to a catalog row"
            assert metric.display_name == display_name

    async def test_the_bare_identity_resolves_to_nothing(self, seeded_catalog: AsyncSession) -> None:
        """Tier one is not an optimisation: skip it and the key resolves to no metric at all."""
        catalog = await Context(session=seeded_catalog).catalog()
        bare = decompose("boltz2.protein_iptm")

        assert bare.identity not in catalog.metric_by_identity

    async def test_a_parameter_key_needs_no_second_tier(self, seeded_catalog: AsyncSession) -> None:
        """The asymmetry: a PARAMETER variant is in the key string, an INTERFACE variant is not."""
        catalog = await Context(session=seeded_catalog).catalog()
        score_key = decompose("evoprotgrad.esm_pseudolikelihood_ratio.H")

        assert score_key.identity == MetricIdentity(
            *_heading("evoprotgrad", "pseudolikelihood_ratio"), VariantKind.PARAMETER, VariantName("esm")
        ), "decompose() filled the variant in from the key string; no candidate was consulted"
        assert catalog.metric_for(score_key, interface_kind=None) is not None

    async def test_an_ordinary_key_resolves_on_identity_alone(self, seeded_catalog: AsyncSession) -> None:
        catalog = await Context(session=seeded_catalog).catalog()
        score_key = decompose("temstapro.clash.H")

        axes = catalog.variant_axes_per_heading.get(score_key.heading, VariantAxes.none())
        assert not axes.interface_qualified, "no entry at all, which is the common case"

        metric = catalog.metric_for(score_key, interface_kind=None)
        assert metric is not None
        assert metric.column_key == "clash"


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


class TestTheLocksAreSeparate:
    """Every lock guards ONE invariant, and no two of them are the same object.

    There are three, and the rule is the same for each. `TaskSafeSession` owns the session's lock,
    which serialises statements. `_catalog_lock` guards "the metric catalog is built once per
    request". `_interface_kinds_lock` guards "the interface-kind map is built once per request".

    Both memo builds issue their statement through `execute_statement()` while holding their own
    lock, which is safe only while that lock is not the session's. Collapse any pair and the task
    waits on a lock it already holds — `asyncio.Lock` is not reentrant — so the request hangs
    forever with no exception and no traceback, while every other request on the loop is served
    normally.

    The two memo locks are also kept apart from EACH OTHER, though that pairing cannot deadlock
    today: the two builds never nest. Sharing would merely serialise two unrelated memos — but it
    would also put one object back in charge of two invariants, which is the shape that produced
    the deadlock in the first place. One lock per invariant is the rule being tested, not "two
    locks happen to be enough".

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

    async def test_the_interface_kinds_lock_is_neither_of_the_others(self, seeded_catalog: AsyncSession) -> None:
        context = Context(session=seeded_catalog)

        assert context._interface_kinds_lock is not context._session._lock
        assert context._interface_kinds_lock is not context._catalog_lock

    async def test_the_interface_map_builds_through_the_ordinary_front_door(self, seeded_catalog: AsyncSession) -> None:
        """`_load_interface_kinds` calls `execute_statement()` while holding its own memo lock."""
        context = Context(session=seeded_catalog)

        interface_kind_per_candidate = await asyncio.wait_for(context.interface_kinds(), timeout=10.0)

        assert interface_kind_per_candidate, "the fixture has three candidates"

    async def test_collapsing_the_interface_lock_into_the_sessions_deadlocks(self, seeded_catalog: AsyncSession) -> None:
        """The same break as above, on the third lock. Reverting the fix must fail loudly here too."""
        context = Context(session=seeded_catalog)
        context._interface_kinds_lock = context._session._lock

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(context.interface_kinds(), timeout=1.0)


class TestInterfaceKindsIsTierTwo:
    """`Context.interface_kinds()` — the candidate-side half of the two-tier lookup.

    `TestTheViewSuppliesTierTwo` above asserts the view's `CASE` against raw rows. This asserts what
    the resolver will actually hold: the same answer, memoized per request, converted to the shared
    vocabulary, and immutable on the way out.
    """

    async def test_every_candidate_gets_the_kind_its_chains_imply(self, seeded_catalog: AsyncSession) -> None:
        """The finding, one layer up from the SQL: chain composition decides what ipTM measures."""
        interface_kind_per_candidate = await Context(session=seeded_catalog).interface_kinds()

        assert interface_kind_per_candidate == {
            "complex-cand": InterfaceKind.ANTIBODY_TARGET_COMPLEX,
            "pairing-cand": InterfaceKind.ANTIBODY_ONLY_HL_PAIRING,
            "lone-cand": InterfaceKind.SINGLE_CHAIN_NO_INTERFACE,
        }

    async def test_the_values_are_enum_members_not_the_raw_case_strings(self, seeded_catalog: AsyncSession) -> None:
        """The str -> enum conversion happens at the SQL boundary, not somewhere downstream.

        Asserted separately from the mapping above because `InterfaceKind.X == "..."` is False for a
        plain `enum.Enum` — so that test would fail on a raw string, but for a reason that reads as
        "wrong kind" rather than "wrong type". This says which.
        """
        interface_kind_per_candidate = await Context(session=seeded_catalog).interface_kinds()

        assert all(isinstance(kind, InterfaceKind) for kind in interface_kind_per_candidate.values())

    async def test_the_map_is_read_only(self, seeded_catalog: AsyncSession) -> None:
        """Handed to every ScoreEntry in the request, so no resolver may corrupt it for the rest.

        The same argument as `MetricCatalog`'s `MappingProxyType`: a plain dict would let one
        resolver rewrite another's answer silently.
        """
        interface_kind_per_candidate = await Context(session=seeded_catalog).interface_kinds()

        with pytest.raises(TypeError):
            interface_kind_per_candidate["complex-cand"] = InterfaceKind.NO_CHAINS_RECORDED  # type: ignore[index]

    async def test_the_interface_memo_survives_concurrent_callers(self, seeded_catalog: AsyncSession) -> None:
        """Fourteen gathered callers, one map — `catalog()`'s measured failure, on the second memo.

        Fourteen for the same reason as the catalog's: it is the corpus's candidate count, and
        `{ candidates { scores } }` gathers the score resolver across that list. Without the
        double-check inside the lock this returns fourteen distinct maps and runs fourteen queries.
        """
        context = Context(session=seeded_catalog)

        maps = await asyncio.gather(*(context.interface_kinds() for _ in range(14)))

        assert len({id(m) for m in maps}) == 1, "every caller must get the same map object"


class TestTheGuardsFailLoudly:
    """Both failure paths in `_load_interface_kinds`, and the reason each is a `GraphQLError`.

    Neither failure is transient: once the data or the code is in the failing state, every query
    that touches scores fails identically until a human intervenes. So each has two audiences — the
    operator who has to fix it, and the client, who otherwise receives `MaskInternalErrors`'
    "Internal server error." and has nothing to report. `test_both_guards_reach_the_client` is the
    one that actually pins that requirement; the two above it only prove the guards fire.
    """

    async def test_a_vendor_id_reused_across_experiments_is_refused(self, seeded_catalog: AsyncSession) -> None:
        """The collision the schema permits and the memo cannot represent.

        `uq_candidate_seq` is `(experiment_id, sequence_id)`, so the SAME vendor id under a SECOND
        experiment is legal — and this map spans every experiment. Folding the two would hand one
        candidate the other's interface kind: heavy-light pairing confidence reported as HER2
        binding, which is the inversion the whole two-tier design exists to prevent.

        Built by inserting the real row rather than by patching the loader, so it exercises the view
        as well as the guard. No collision exists in the real corpus (14 candidates, 14 distinct
        ids), which is exactly why the case has to be constructed to be tested at all.
        """
        second_run = db.Experiment(name="second run", source_filename="second.csv")
        seeded_catalog.add(second_run)
        await seeded_catalog.flush()

        seeded_catalog.add(
            db.Candidate(
                experiment_id=second_run.id,
                sequence_id="complex-cand",  # already used by a candidate in "fixture run"
                scores={},
                chains=[db.CandidateChain(role=db.ChainRole.HEAVY, sequence="QVQ", ordinal=0)],
            )
        )
        await seeded_catalog.flush()

        with pytest.raises(GraphQLError) as raised:
            await Context(session=seeded_catalog).interface_kinds()

        assert (raised.value.extensions or {})["code"] == "AMBIGUOUS_CANDIDATE_ID"
        assert "complex-cand" in raised.value.message, "the message must name the offending id"

    async def test_a_view_label_with_no_enum_member_is_refused(self, seeded_catalog: AsyncSession) -> None:
        """Drift between the view's CASE and `InterfaceKind`.

        The real `CASE` cannot emit an unknown label, so the only honest way to reach this branch is
        to replace the view — which is safe here and nowhere else: the test database is a
        throwaway testcontainer, and the replacement rolls back with the fixture's transaction along
        with everything else the test wrote. Preferred to patching the loader because it tests the
        boundary that would actually drift.

        `tests/test_interface_kind.py` catches this statically in CI, without a database. This is
        the belt to that suspenders, and it exists to pin the ERROR SHAPE rather than the detection.
        """
        await seeded_catalog.execute(text("""
                CREATE OR REPLACE VIEW candidate_summary AS
                SELECT e.name                 AS experiment,
                       c.sequence_id          AS candidate_id,
                       left(c.sequence_id, 8) AS candidate,
                       NULL::text             AS chains,
                       NULL::text             AS antibody_hash,
                       'a kind that does not exist'::text AS interface_kind,
                       -- count(*) is bigint; an int literal here is rejected outright by
                       -- CREATE OR REPLACE VIEW, which cannot change a column's type.
                       0::bigint              AS n_scores,
                       NULL::numeric          AS iptm,
                       NULL::numeric          AS complex_plddt,
                       NULL::numeric          AS humanness_oasis,
                       NULL::numeric          AS humatch_human,
                       NULL::text             AS thermo_class,
                       NULL::int              AS epitope_residues,
                       NULL::text             AS epitope_list
                  FROM candidates c JOIN experiments e ON e.id = c.experiment_id
                """))

        with pytest.raises(GraphQLError) as raised:
            await Context(session=seeded_catalog).interface_kinds()

        assert (raised.value.extensions or {})["code"] == "UNKNOWN_INTERFACE_KIND"
        assert "a kind that does not exist" in raised.value.message

    @pytest.mark.parametrize("code", ["AMBIGUOUS_CANDIDATE_ID", "UNKNOWN_INTERFACE_KIND"])
    def test_both_guards_reach_the_client(self, code: str) -> None:
        """The requirement neither test above covers: the caller is told WHAT broke.

        `MaskInternalErrors` replaces the message of any error without a deliberate `code`, so a
        bare `ValueError` here would arrive as "Internal server error." — non-transient, permanent,
        and unreportable. The codes are what survive that, and nothing else in the suite checks it.

        The control case matters as much as the two codes: it is what would catch `should_mask_error`
        being inverted, which would let every internal error through while these two still passed.

        EVERY ERROR HERE CARRIES AN `original_error`, because that is what a resolver-raised error
        looks like by the time the extension sees it — graphql-core wraps whatever a resolver raised.
        A `GraphQLError("boom")` built bare has NO original, which is the signature of GraphQL's own
        syntax/validation errors, and those are deliberately not masked. The first draft of this
        test built the control bare, and it went red the moment that rule landed: it was asserting
        "internal errors are masked" against an error that was not internal.
        """
        internal = GraphQLError("boom", original_error=RuntimeError("boom"))
        assert should_mask_error(internal), "control: an uncoded error out of a resolver IS masked"

        deliberate = GraphQLError("boom", original_error=ValueError("boom"), extensions={"code": code})
        assert not should_mask_error(deliberate)

        graphqls_own = GraphQLError("Cannot query field 'nosuchfield' on type 'Query'.")
        assert not should_mask_error(graphqls_own), "a syntax/validation error has no original and must reach the client"
