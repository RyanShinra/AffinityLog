"""Stage 4 of docs/scoreentry-plan.md — Candidate.interfaceKind, .target, .artifacts.

WRITTEN BEFORE THE CODE. Three small fields, each with one decision behind it:

  * `interfaceKind` is NON-NULL. A candidate missing from `candidate_summary` therefore cannot
    return null without blanking the whole Candidate, so it raises a coded error instead —
    the same treatment `metric_for` gives the same absence.
  * `target` is a two-hop hoist (candidate -> experiment -> target) served by ONE join, not by
    reusing `Experiment.select_statement()` and its three eager loads.
  * `artifacts` ships EMPTY (decided 2026-09-18): nothing writes the table yet, and the eleven
    structures live on disk. The mapping is exercised here by inserting a row inside the
    rolled-back transaction, so a green test means the shape is right, not that data exists.

The fixture's three candidates have chains [H, L, T], [H, L] and [H], which reach three of the
view's four CASE arms. The fourth, NO_CHAINS_RECORDED, is reached by adding a chainless candidate
in the test that needs it.
"""

from __future__ import annotations

from typing import Any

import pytest
from graphql import GraphQLError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identifiers import SequenceId
from app.graphql.context import Context
from app.graphql.schema import schema
from app.graphql.types import _interface_kind_of
from app.models import orm as db


async def _query(session: AsyncSession, document: str, **variables: Any) -> dict[str, Any]:
    """Run one GraphQL document, failing loudly on any error. Copied from test_catalog_types.py."""
    result = await schema.execute(document, variable_values=variables or None, context_value=Context(session=session))
    assert result.errors is None, f"query raised: {[str(e) for e in result.errors]}"
    assert result.data is not None
    return result.data


def _by_sequence_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for candidate in data["candidates"]:
        by_id[candidate["sequenceId"]] = candidate
    return by_id


class TestInterfaceKind:
    """The candidate-side half of the two-tier lookup, exposed."""

    async def test_three_candidates_three_kinds(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(seeded_catalog, "{ candidates { sequenceId interfaceKind } }")

        kinds = {sequence_id: c["interfaceKind"] for sequence_id, c in _by_sequence_id(data).items()}

        assert kinds == {
            "complex-cand": "ANTIBODY_TARGET_COMPLEX",
            "pairing-cand": "ANTIBODY_ONLY_HL_PAIRING",
            "lone-cand": "SINGLE_CHAIN_NO_INTERFACE",
        }

    async def test_a_chainless_candidate_reports_no_chains_recorded(self, seeded_catalog: AsyncSession) -> None:
        """The fourth member is a data-quality state, and it is reachable: the view LEFT JOINs chains."""
        experiment_id = (await seeded_catalog.execute(select(db.Experiment.id))).scalar_one()
        seeded_catalog.add(db.Candidate(experiment_id=experiment_id, sequence_id="chainless-cand", scores={}))
        await seeded_catalog.flush()

        data = await _query(seeded_catalog, "{ candidates { sequenceId interfaceKind } }")

        assert _by_sequence_id(data)["chainless-cand"]["interfaceKind"] == "NO_CHAINS_RECORDED"

    async def test_the_finding_in_one_query(self, seeded_catalog: AsyncSession) -> None:
        """The plan's "How to know it worked" query, verbatim now that interfaceKind exists."""
        data = await _query(
            seeded_catalog,
            """
            { candidates { sequenceId interfaceKind
                scores(module: "boltz2") { key value metric { displayName } } } }
            """,
        )

        pairs = set()
        for candidate in data["candidates"]:
            (iptm,) = candidate["scores"]
            pairs.add((candidate["interfaceKind"], iptm["metric"]["displayName"]))

        assert pairs == {
            ("ANTIBODY_TARGET_COMPLEX", "HER2 binding confidence"),
            ("ANTIBODY_ONLY_HL_PAIRING", "Heavy-light pairing confidence"),
            ("SINGLE_CHAIN_NO_INTERFACE", "Not an interface"),
        }

    def test_a_candidate_missing_from_the_view_is_a_coded_error(self) -> None:
        """Non-null field, so absence cannot be null. The helper alone, with an empty map."""
        with pytest.raises(GraphQLError) as raised:
            _interface_kind_of(SequenceId("ghost"), {})

        assert raised.value.extensions is not None
        assert raised.value.extensions["code"] == "CANDIDATE_NOT_IN_SUMMARY"
        assert "ghost" in str(raised.value)


class TestTarget:
    """Two FK hops flattened to one field."""

    async def test_target_is_null_when_the_experiment_has_none(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(seeded_catalog, "{ candidates { sequenceId target { name } } }")

        assert all(c["target"] is None for c in data["candidates"])

    async def test_target_is_hoisted_through_the_experiment(self, seeded_catalog: AsyncSession) -> None:
        her2 = db.Target(name="HER2", pdb_id="1N8Z")
        seeded_catalog.add(her2)
        await seeded_catalog.flush()
        experiment = (await seeded_catalog.execute(select(db.Experiment))).scalar_one()
        experiment.target_id = her2.id
        await seeded_catalog.flush()

        data = await _query(seeded_catalog, "{ candidates { sequenceId target { name pdbId } } }")

        assert _by_sequence_id(data)["complex-cand"]["target"] == {"name": "HER2", "pdbId": "1N8Z"}


class TestArtifacts:
    """Ships empty by decision. The mapping is proven with a row that never leaves the transaction."""

    async def test_artifacts_is_an_empty_list_not_null(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(seeded_catalog, "{ candidates { sequenceId artifacts { kind uri } } }")

        assert all(c["artifacts"] == [] for c in data["candidates"])

    async def test_an_artifact_row_round_trips(self, seeded_catalog: AsyncSession) -> None:
        candidate = (
            await seeded_catalog.execute(select(db.Candidate).where(db.Candidate.sequence_id == "complex-cand"))
        ).scalar_one()
        seeded_catalog.add(db.Artifact(candidate_id=candidate.id, kind="structure", uri="/demo/pdb/abc123"))
        await seeded_catalog.flush()

        data = await _query(seeded_catalog, "{ candidates { sequenceId artifacts { kind uri } } }")
        by_id = _by_sequence_id(data)

        assert by_id["complex-cand"]["artifacts"] == [{"kind": "structure", "uri": "/demo/pdb/abc123"}]
        assert by_id["lone-cand"]["artifacts"] == [], "filtered to the owning candidate"


class TestTheSdlItself:
    def test_the_four_interface_kinds_are_in_the_schema(self) -> None:
        sdl = str(schema)

        assert "enum InterfaceKind {" in sdl
        for member in (
            "ANTIBODY_TARGET_COMPLEX",
            "ANTIBODY_ONLY_HL_PAIRING",
            "SINGLE_CHAIN_NO_INTERFACE",
            "NO_CHAINS_RECORDED",
        ):
            assert member in sdl

    def test_the_three_fields_and_the_type(self) -> None:
        sdl = str(schema)

        assert "interfaceKind: InterfaceKind!" in sdl
        assert "target: Target" in sdl
        assert "artifacts: [Artifact!]!" in sdl
        assert "type Artifact {" in sdl
