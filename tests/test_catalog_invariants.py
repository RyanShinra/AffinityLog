"""The write-time check on `(module, column_key) -> variant_kind`.

The interesting cases are the two it must NOT flag. A write-time check that refuses a legitimate
shape is worse than no check, because it blocks the work rather than the error — and the first
version of this one did exactly that to a catalog addition the project has already written down.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identifiers import ColumnKey, ModuleName, VariantName
from app.catalog.invariants import Heading, find_heading_violations, raise_on_heading_violations
from app.catalog.keys import decompose
from app.catalog.variant_kind import VariantKind
from app.graphql.context import Context
from app.models import orm as db


async def _module(session: AsyncSession, name: str) -> db.Module:
    module = db.Module(name=name, module_type=db.ModuleType.SCORE, functions=[])
    session.add(module)
    await session.flush()
    return module


def _metric(module: db.Module, column_key: str, **kwargs: object) -> db.Metric:
    defaults: dict[str, object] = {
        "display_name": column_key,
        "value_type": db.MetricValueType.FLOAT,
        "direction": db.Direction.NEUTRAL,
        "provenance": db.Provenance.INFERRED,
    }
    return db.Metric(module_id=module.id, column_key=column_key, **{**defaults, **kwargs})


def _heading(
    module: str,
    column_key: str,
    variant_kinds: tuple[VariantKind, ...] = (),
    variants: tuple[str, ...] = (),
    bare_rows: int = 0,
) -> Heading:
    """A Heading from plain strings — see `_key` in test_catalog_keys.py for why the `NewType`
    laundering is safe in a test and would not be in production code."""
    return Heading(
        ModuleName(module),
        ColumnKey(column_key),
        variant_kinds,
        tuple(VariantName(v) for v in variants),
        bare_rows,
    )


class TestShapesItMustAccept:
    async def test_a_bare_row_beside_a_transform_row(self, session: AsyncSession) -> None:
        """The raw / `-transformed` pair, which seed/catalog.json names as the next addition.

        `fastdpe.SFvCSP`: "A raw and a '-transformed' form exist; when the transformed variant is
        seeded it uses VariantKind.TRANSFORM." One heading, a bare row and a TRANSFORM row.

        The first version of this check flagged it — "has 1 variant-less row(s) beside
        ['TRANSFORM'], which can never be reached" — on the reasoning that tier one always
        qualifies. It does not, and this test is the proof: the lookup resolves the bare row fine,
        because only INTERFACE triggers qualification.
        """
        fastdpe = await _module(session, "fastdpe")
        session.add_all(
            [
                _metric(fastdpe, "SFvCSP", display_name="SFvCSP (Fv charge symmetry)"),
                _metric(
                    fastdpe,
                    "SFvCSP",
                    display_name="SFvCSP-transformed",
                    variant_kind=VariantKind.TRANSFORM,
                    variant="transformed",
                ),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []

        # ...and the reason it is not a violation: the bare row is genuinely reachable.
        catalog = await Context(session=session).catalog()
        key = decompose("fastdpe.SFvCSP")
        resolved = catalog.metric_by_identity[key.identity]
        assert resolved.display_name == "SFvCSP (Fv charge symmetry)"

    async def test_a_bare_row_beside_a_transform_row_on_a_heading_that_has_a_variant_rule(self, session: AsyncSession) -> None:
        """The same legitimate shape as above, on a heading that happens to appear in _VARIANT_RULES.

        `evoprotgrad.pseudolikelihood_ratio` has a PARAMETER rule. That says what its key strings
        encode along the PARAMETER axis, and nothing whatsoever about TRANSFORM. A TRANSFORM row
        beside a bare row is exactly the shape `fastdpe.SFvCSP` is allowed above — whether the
        heading also has a rule for an unrelated axis cannot be what decides it.
        """
        evoprotgrad = await _module(session, "evoprotgrad")
        session.add_all(
            [
                _metric(evoprotgrad, "pseudolikelihood_ratio"),
                _metric(
                    evoprotgrad,
                    "pseudolikelihood_ratio",
                    variant_kind=VariantKind.TRANSFORM,
                    variant="transformed",
                ),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []

    async def test_one_axis_with_several_variants(self, session: AsyncSession) -> None:
        """The evoprotgrad shape already in the corpus: two PARAMETER rows, no bare row."""
        evoprotgrad = await _module(session, "evoprotgrad")
        session.add_all(
            [
                _metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=VariantKind.PARAMETER, variant="esm"),
                _metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=VariantKind.PARAMETER, variant="amplify"),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []


class TestShapesItMustRefuse:
    async def test_a_bare_row_beside_interface_rows(self, session: AsyncSession) -> None:
        """The genuinely unreachable case: tier one qualifies, so the bare row is dead data."""
        boltz2 = await _module(session, "boltz2")
        session.add_all(
            [
                _metric(boltz2, "protein_iptm"),  # bare, and unreachable
                _metric(boltz2, "protein_iptm", variant_kind=VariantKind.INTERFACE, variant="antibody-target complex"),
            ]
        )
        await session.flush()

        violations = await find_heading_violations(session)

        assert len(violations) == 1
        assert "can never be reached" in violations[0].describe()

    async def test_two_axes_under_one_heading(self, session: AsyncSession) -> None:
        """No identity can name both: a metric row carries a single (variant_kind, variant) pair."""
        boltz2 = await _module(session, "boltz2")
        session.add_all(
            [
                _metric(boltz2, "iptm", variant_kind=VariantKind.INTERFACE, variant="antibody-target complex"),
                _metric(boltz2, "iptm", variant_kind=VariantKind.PARAMETER, variant="esm"),
            ]
        )
        await session.flush()

        violations = await find_heading_violations(session)

        assert len(violations) == 1
        assert "2 axes" in violations[0].describe()

    async def test_both_reasons_are_reported_together(self, session: AsyncSession) -> None:
        """`problems()` returns every applicable reason, not the first one.

        Reporting only the first would send the operator round twice: fix the axes, re-run, fail
        again on the orphan that was in the tuple all along.

        (This said "the two HAVING arms". There is no HAVING — the query returns every heading and
        `Heading.problems()` classifies in Python, because two of the branches need sets imported
        from app.catalog.keys and app.catalog.interface_kind that SQL cannot see. There are four
        branches now, not two.)
        """
        boltz2 = await _module(session, "boltz2")
        session.add_all(
            [
                _metric(boltz2, "complex_ipde"),  # bare
                _metric(boltz2, "complex_ipde", variant_kind=VariantKind.INTERFACE, variant="antibody-target complex"),
                _metric(boltz2, "complex_ipde", variant_kind=VariantKind.PARAMETER, variant="esm"),
            ]
        )
        await session.flush()

        described = (await find_heading_violations(session))[0].describe()

        assert "2 axes" in described
        assert "can never be reached" in described

    async def test_raising_names_the_source_without_blaming_it(self, session: AsyncSession) -> None:
        """The check reads the whole table, so the rows may predate the run that trips it."""
        boltz2 = await _module(session, "boltz2")
        session.add_all(
            [
                _metric(boltz2, "protein_iptm"),
                _metric(boltz2, "protein_iptm", variant_kind=VariantKind.INTERFACE, variant="antibody-target complex"),
            ]
        )
        await session.flush()

        with pytest.raises(ValueError) as caught:
            await raise_on_heading_violations(session, source="seed_catalog")

        message = str(caught.value)
        assert "seed_catalog refused to commit" in message
        assert "may PREDATE it" in message


class TestAnAxisNothingCanSupply:
    """The third way a heading breaks the lookup, and the quietest.

    `decompose()` recovers exactly one axis from a key string — PARAMETER, from the single
    evoprotgrad rule. Tier two supplies exactly one more — INTERFACE. A heading qualified along any
    of the other four (MODE, SOURCE_MODEL, COMPONENT, TRANSFORM) with no variant-less row to fall
    back to therefore resolves to NOTHING, for every candidate, with no exception and no log.

    This is classified in Python rather than in the query's HAVING because it needs
    `decomposable_kinds_for(module, column_key)` from app/catalog/keys.py — and it is keyed by
    HEADING, not by kind, because the rules there are pinned to a module and a column.
    """

    async def test_a_heading_qualified_only_by_transform_is_refused(self, session: AsyncSession) -> None:
        fastdpe = await _module(session, "fastdpe")
        session.add_all(
            [
                _metric(fastdpe, "SFvCSP", variant_kind=VariantKind.TRANSFORM, variant="raw"),
                _metric(fastdpe, "SFvCSP", variant_kind=VariantKind.TRANSFORM, variant="transformed"),
            ]
        )
        await session.flush()

        violations = await find_heading_violations(session)

        assert len(violations) == 1
        assert "nothing can supply" in violations[0].describe()

    async def test_the_same_axis_is_fine_with_a_bare_row_to_fall_back_to(self, session: AsyncSession) -> None:
        """Which is exactly the shape seed/catalog.json plans, and why the check is not simply
        'refuse TRANSFORM'."""
        fastdpe = await _module(session, "fastdpe")
        session.add_all(
            [
                _metric(fastdpe, "SFvCSP"),
                _metric(fastdpe, "SFvCSP", variant_kind=VariantKind.TRANSFORM, variant="transformed"),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []

    async def test_parameter_is_not_satisfiable_under_a_column_the_rule_cannot_produce(self, session: AsyncSession) -> None:
        """The satisfiability check's own false negative.

        `_VARIANT_RULES` is keyed by MODULE and its regex pins the COLUMN, so the only heading whose
        PARAMETER variant `decompose()` can recover is `evoprotgrad.pseudolikelihood_ratio`. Any
        other column under the same module is unreachable:

            decompose('evoprotgrad.esm_entropy') -> (evoprotgrad, esm_entropy, None, None)

        `DECOMPOSABLE_VARIANT_KINDS` flattens the rules to the bare kind name {'PARAMETER'} and so
        answers "is this kind decomposable?" when the answerable question is "is it decomposable
        FOR THIS HEADING?".
        """
        evoprotgrad = await _module(session, "evoprotgrad")
        session.add_all(
            [
                _metric(evoprotgrad, "entropy", variant_kind=VariantKind.PARAMETER, variant="esm"),
                _metric(evoprotgrad, "entropy", variant_kind=VariantKind.PARAMETER, variant="amplify"),
            ]
        )
        await session.flush()

        violations = await find_heading_violations(session)

        assert len(violations) == 1
        assert "nothing can supply" in violations[0].describe()

    async def test_parameter_is_not_satisfiable_in_a_module_with_no_rule_at_all(self, session: AsyncSession) -> None:
        """Same hole, reached from the other side: boltz2 has no entry in `_VARIANT_RULES`."""
        boltz2 = await _module(session, "boltz2")
        session.add_all(
            [
                _metric(boltz2, "pseudolikelihood_ratio", variant_kind=VariantKind.PARAMETER, variant="esm"),
                _metric(boltz2, "pseudolikelihood_ratio", variant_kind=VariantKind.PARAMETER, variant="amplify"),
            ]
        )
        await session.flush()

        violations = await find_heading_violations(session)

        assert len(violations) == 1
        assert "nothing can supply" in violations[0].describe()

    async def test_parameter_is_satisfiable_because_the_key_string_carries_it(self, session: AsyncSession) -> None:
        """No bare row, no INTERFACE — and still fine, because decompose() reads `esm_` off the key."""
        evoprotgrad = await _module(session, "evoprotgrad")
        session.add_all(
            [
                _metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=VariantKind.PARAMETER, variant="esm"),
                _metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=VariantKind.PARAMETER, variant="amplify"),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []


class TestAnAxisWithMissingValues:
    """The fourth way a heading breaks the lookup: qualified along an axis it does not cover.

    The first three ask whether an axis can be RESOLVED at all. This asks whether every value of a
    resolvable axis is actually catalogued — because tier two builds
    (module, column_key, 'INTERFACE', <the candidate's interface kind>) from the candidate, and if
    no row was seeded for that particular kind the key resolves to nothing. Silently, for exactly
    the candidates carrying that kind, while every other candidate resolves fine. That partial
    failure is harder to notice than a total one.

    All three scoreable interface kinds occur in the real corpus (6 / 4 / 4 of the 14 candidates),
    so a heading seeded with two of three is a live bug, not a hypothetical.
    """

    async def test_interface_rows_must_cover_every_scoreable_kind(self, session: AsyncSession) -> None:
        boltz2 = await _module(session, "boltz2")
        session.add_all(
            [
                _metric(boltz2, "protein_iptm", variant_kind=VariantKind.INTERFACE, variant="antibody-target complex"),
                _metric(
                    boltz2,
                    "protein_iptm",
                    variant_kind=VariantKind.INTERFACE,
                    variant="antibody only (H/L pairing)",
                ),
                # 'single chain (no interface)' is missing. Four of the 14 real candidates have it.
            ]
        )
        await session.flush()

        violations = await find_heading_violations(session)

        assert len(violations) == 1
        assert "single chain (no interface)" in violations[0].describe()

    async def test_the_no_chains_arm_is_not_required(self, session: AsyncSession) -> None:
        """The exemption, which the fixtures already state as project policy.

        `tests/conftest.py` calls the fourth arm "a data-quality state" and leaves it
        unrepresented; `sql/candidate_summary.sql` adds it defensively, so `bool_or` over zero
        chain rows cannot return NULL and fall through to the ELSE. A candidate with no chains has
        no interface to score, and the only recipe that could produce one (ESM2) emits no boltz2
        columns at all. So a row for it would be dead data, and demanding one would flag the live
        corpus three times over.
        """
        boltz2 = await _module(session, "boltz2")
        session.add_all(
            [
                _metric(boltz2, "protein_iptm", variant_kind=VariantKind.INTERFACE, variant="antibody-target complex"),
                _metric(
                    boltz2,
                    "protein_iptm",
                    variant_kind=VariantKind.INTERFACE,
                    variant="antibody only (H/L pairing)",
                ),
                _metric(
                    boltz2,
                    "protein_iptm",
                    variant_kind=VariantKind.INTERFACE,
                    variant="single chain (no interface)",
                ),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []

    async def test_parameter_rows_must_cover_every_variant_the_rule_declares(self, session: AsyncSession) -> None:
        """The same question on the other axis, askable only since the rules table carries `variants`.

        `evoprotgrad.esm_pseudolikelihood_ratio` and `...amplify_...` both appear in the corpus. Seed
        one arm and the other resolves to nothing.
        """
        evoprotgrad = await _module(session, "evoprotgrad")
        session.add(_metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=VariantKind.PARAMETER, variant="esm"))
        await session.flush()

        violations = await find_heading_violations(session)

        assert len(violations) == 1
        assert "amplify" in violations[0].describe()


class TestTheDatabaseRefusesHalfPopulatedRows:
    """`variant_kind` and `variant` are both-or-neither, enforced by migration 008.

    A row with exactly one populated is unreachable — `decompose()` never emits a variant without a
    kind, and an INTERFACE row with a NULL variant can never match
    (module, column_key, 'INTERFACE', <interface_kind>). It is dead data, and it also HID things:
    `_HEADINGS_SQL` counts `variant_kind IS NULL` as a bare row, and a bare row is what suppresses
    the unsatisfiable-axis branch, so one malformed row silenced that check for a whole heading.

    That suppression has no test here because it is no longer expressible — the row cannot be
    written. This class asserts the constraint instead, which is the enforcement it was traded for.
    A Python-side branch reporting the same thing would be dead code.

    Note this is the OPPOSITE call to the rest of the module. The functional dependency
    `(module_id, column_key) -> variant_kind` cannot be expressed by any constraint, so it is
    checked at write time; both-or-neither is a plain CHECK, so it is enforced by the schema.
    """

    async def test_a_variant_without_a_kind_is_rejected(self, session: AsyncSession) -> None:
        temstapro = await _module(session, "temstapro")
        # `pytest.raises` OUTSIDE the savepoint, deliberately. `AsyncSession.begin_nested()` is a
        # plain `def` returning an AsyncSessionTransaction, so calling it bare — as this did —
        # constructs the handle and discards it without ever issuing a SAVEPOINT; the failing
        # statement then poisons the FIXTURE's savepoint instead. Entering it with `async with`
        # issues the real one, and letting the error escape the block lets __aexit__ roll it back.
        # Catching inside would exit the block normally and try to RELEASE a savepoint Postgres has
        # already aborted.
        with pytest.raises(IntegrityError, match="ck_metric_variant_pair"):
            async with session.begin_nested():
                session.add(_metric(temstapro, "clash", variant="esm"))
                await session.flush()

    async def test_a_kind_without_a_variant_is_rejected(self, session: AsyncSession) -> None:
        """The mirror case, which the symmetric expression catches for free."""
        temstapro = await _module(session, "temstapro")
        # `pytest.raises` OUTSIDE the savepoint, deliberately. `AsyncSession.begin_nested()` is a
        # plain `def` returning an AsyncSessionTransaction, so calling it bare — as this did —
        # constructs the handle and discards it without ever issuing a SAVEPOINT; the failing
        # statement then poisons the FIXTURE's savepoint instead. Entering it with `async with`
        # issues the real one, and letting the error escape the block lets __aexit__ roll it back.
        # Catching inside would exit the block normally and try to RELEASE a savepoint Postgres has
        # already aborted.
        with pytest.raises(IntegrityError, match="ck_metric_variant_pair"):
            async with session.begin_nested():
                session.add(_metric(temstapro, "clash", variant_kind=VariantKind.PARAMETER))
                await session.flush()

    async def test_both_clean_shapes_are_still_accepted(self, session: AsyncSession) -> None:
        """The check that keeps the constraint from being over-broad.

        133 of the corpus's 144 metrics are bare and 11 are fully qualified; a constraint that
        refused either would refuse the real catalog.
        """
        temstapro = await _module(session, "temstapro")
        session.add_all(
            [
                _metric(temstapro, "clash"),
                _metric(temstapro, "clash", variant_kind=VariantKind.PARAMETER, variant="esm"),
            ]
        )

        await session.flush()  # raises if either shape is refused


class TestProblemsNeedsNoDatabase:
    """`Heading.problems()` is a pure function, so the classification is testable on its own."""

    def test_a_clean_heading_has_no_problems(self) -> None:
        assert _heading("boltz2", "ptm", bare_rows=1).problems() == ()

    def test_every_applicable_reason_is_reported(self) -> None:
        both = _heading("boltz2", "iptm", (VariantKind.INTERFACE, VariantKind.PARAMETER), bare_rows=2).problems()

        assert len(both) == 2
        assert any("2 axes" in reason for reason in both)
        assert any("can never be reached" in reason for reason in both)

    def test_coverage_is_only_asked_of_a_single_axis(self) -> None:
        """Two axes is already refused, and the query aggregates variants across the whole heading
        rather than per kind — so asking about coverage there would compare against the wrong set."""
        mixed = _heading("boltz2", "iptm", (VariantKind.INTERFACE, VariantKind.PARAMETER), ("esm",)).problems()

        assert not any("has no row for" in reason for reason in mixed)

    def test_a_partly_covered_interface_heading_names_what_is_missing(self) -> None:
        partial = _heading(
            "boltz2",
            "protein_iptm",
            (VariantKind.INTERFACE,),
            ("antibody-target complex", "antibody only (H/L pairing)"),
        ).problems()

        assert len(partial) == 1
        assert "single chain (no interface)" in partial[0]
        assert "no chains recorded" not in partial[0], "the fourth arm is deliberately not required"
