"""The write-time check on `(module, column_key) -> variant_kind`.

The interesting cases are the two it must NOT flag. A write-time check that refuses a legitimate
shape is worse than no check, because it blocks the work rather than the error — and the first
version of this one did exactly that to a catalog addition the project has already written down.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.invariants import Heading, find_heading_violations, raise_on_heading_violations
from app.catalog.keys import decompose
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
                    variant_kind=db.VariantKind.TRANSFORM,
                    variant="transformed",
                ),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []

        # ...and the reason it is not a violation: the bare row is genuinely reachable.
        catalog = await Context(session=session).catalog()
        key = decompose("fastdpe.SFvCSP")
        resolved = catalog.metric_by_identity[(key.module, key.column_key, key.variant_kind, key.variant)]
        assert resolved.display_name == "SFvCSP (Fv charge symmetry)"

    async def test_one_axis_with_several_variants(self, session: AsyncSession) -> None:
        """The evoprotgrad shape already in the corpus: two PARAMETER rows, no bare row."""
        evoprotgrad = await _module(session, "evoprotgrad")
        session.add_all(
            [
                _metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=db.VariantKind.PARAMETER, variant="esm"),
                _metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=db.VariantKind.PARAMETER, variant="amplify"),
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
                _metric(boltz2, "protein_iptm", variant_kind=db.VariantKind.INTERFACE, variant="antibody-target complex"),
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
                _metric(boltz2, "iptm", variant_kind=db.VariantKind.INTERFACE, variant="antibody-target complex"),
                _metric(boltz2, "iptm", variant_kind=db.VariantKind.PARAMETER, variant="esm"),
            ]
        )
        await session.flush()

        violations = await find_heading_violations(session)

        assert len(violations) == 1
        assert "2 axes" in violations[0].describe()

    async def test_both_reasons_are_reported_together(self, session: AsyncSession) -> None:
        """The two HAVING arms are independent, so one heading can trip both.

        Reporting only the first would send the operator round twice: fix the axes, re-run, fail
        again on the orphan that was in the tuple all along.
        """
        boltz2 = await _module(session, "boltz2")
        session.add_all(
            [
                _metric(boltz2, "complex_ipde"),  # bare
                _metric(boltz2, "complex_ipde", variant_kind=db.VariantKind.INTERFACE, variant="antibody-target complex"),
                _metric(boltz2, "complex_ipde", variant_kind=db.VariantKind.PARAMETER, variant="esm"),
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
                _metric(boltz2, "protein_iptm", variant_kind=db.VariantKind.INTERFACE, variant="antibody-target complex"),
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
    `DECOMPOSABLE_VARIANT_KINDS`, which is derived from the rules in app/catalog/keys.py so the two
    cannot drift apart.
    """

    async def test_a_heading_qualified_only_by_transform_is_refused(self, session: AsyncSession) -> None:
        fastdpe = await _module(session, "fastdpe")
        session.add_all(
            [
                _metric(fastdpe, "SFvCSP", variant_kind=db.VariantKind.TRANSFORM, variant="raw"),
                _metric(fastdpe, "SFvCSP", variant_kind=db.VariantKind.TRANSFORM, variant="transformed"),
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
                _metric(fastdpe, "SFvCSP", variant_kind=db.VariantKind.TRANSFORM, variant="transformed"),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []

    async def test_parameter_is_satisfiable_because_the_key_string_carries_it(self, session: AsyncSession) -> None:
        """No bare row, no INTERFACE — and still fine, because decompose() reads `esm_` off the key."""
        evoprotgrad = await _module(session, "evoprotgrad")
        session.add_all(
            [
                _metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=db.VariantKind.PARAMETER, variant="esm"),
                _metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=db.VariantKind.PARAMETER, variant="amplify"),
            ]
        )
        await session.flush()

        assert await find_heading_violations(session) == []


class TestProblemsNeedsNoDatabase:
    """`Heading.problems()` is a pure function, so the classification is testable on its own."""

    def test_a_clean_heading_has_no_problems(self) -> None:
        assert Heading("boltz2", "ptm", (), 1).problems() == ()

    def test_every_applicable_reason_is_reported(self) -> None:
        both = Heading("boltz2", "iptm", ("INTERFACE", "PARAMETER"), 2).problems()

        assert len(both) == 2
        assert any("2 axes" in reason for reason in both)
        assert any("can never be reached" in reason for reason in both)
