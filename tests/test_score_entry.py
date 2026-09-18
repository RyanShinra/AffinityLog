"""Stage 2 of docs/scoreentry-plan.md — ScoreEntry and Candidate.scores.

WRITTEN BEFORE THE CODE. Expected to fail with "Cannot query field 'scores'" until `ScoreEntry`
exists in `app/graphql/types.py` and `Candidate` grows the resolver.

The headline test is the plan's own success criterion: one JSONB key, `boltz2.protein_iptm`,
returning THREE different display names in one response, chosen by what each candidate folded.
That is the 2026-07-31 finding served over the API, and nothing demonstrates it before this file.

Everything goes through `schema.execute()`, for the stage-3 reason: the contract is the SDL, and a
resolver that returns the right object under the wrong declared type is what
`tests/test_schema_snapshot.py` exists to catch. The fixture gives every candidate the same six
keys, so the tests below pick a candidate by `sequenceId` and assert on its entries by `key`.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.keys import decompose
from app.graphql.context import Context
from app.graphql.schema import schema
from app.graphql.types import _numeric_value, _passes_filters
from app.models import orm as db


async def _query(session: AsyncSession, document: str, **variables: Any) -> dict[str, Any]:
    """Run one GraphQL document, failing loudly on any error. Copied from test_catalog_types.py."""
    result = await schema.execute(document, variable_values=variables or None, context_value=Context(session=session))
    assert result.errors is None, f"query raised: {[str(e) for e in result.errors]}"
    assert result.data is not None
    return result.data


def _entries_by_key(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index one candidate's `scores` list by its raw key, so a test can ask for a branch by name."""
    by_key: dict[str, dict[str, Any]] = {}
    for entry in candidate["scores"]:
        by_key[entry["key"]] = entry
    return by_key


class TestTheFinding:
    """The reason the chapter exists."""

    async def test_one_key_returns_three_display_names(self, seeded_catalog: AsyncSession) -> None:
        """`boltz2.protein_iptm` means three different things across three candidates.

        The plan's "How to know it worked" query, minus `interfaceKind`, which is stage 4.
        """
        data = await _query(
            seeded_catalog,
            """
            { candidates { sequenceId scores { key value metric { displayName } } } }
            """,
        )

        display_name_per_candidate: dict[str, str | None] = {}
        for candidate in data["candidates"]:
            iptm = _entries_by_key(candidate)["boltz2.protein_iptm"]
            display_name_per_candidate[candidate["sequenceId"]] = iptm["metric"]["displayName"]

        assert display_name_per_candidate == {
            "complex-cand": "HER2 binding confidence",
            "pairing-cand": "Heavy-light pairing confidence",
            "lone-cand": "Not an interface",
        }


class TestTheEntryFields:
    """`key`, `value`, and the order they come back in."""

    async def test_value_is_the_raw_string_and_entries_are_sorted_by_key(self, seeded_catalog: AsyncSession) -> None:
        """`value` is served exactly as stored: "<40" stays "<40", "2" stays "2", never 2.0.

        Order is asserted too, because JSONB does not preserve insertion order and a list whose
        order depends on Postgres's key-hashing is a flaky test waiting to happen.
        """
        data = await _query(
            seeded_catalog,
            """
            { candidate: candidates { sequenceId scores { key value } } }
            """,
        )

        complex_cand = next(c for c in data["candidate"] if c["sequenceId"] == "complex-cand")
        keys_in_order = [entry["key"] for entry in complex_cand["scores"]]
        values_by_key = {entry["key"]: entry["value"] for entry in complex_cand["scores"]}

        assert keys_in_order == sorted(keys_in_order)
        assert keys_in_order == [
            "boltz2.protein_iptm",
            "mystery.column",
            "temstapro.clash",
            "temstapro.clash.H",
            "temstapro.clash.L",
            "temstapro.verdict",
        ]
        assert values_by_key["temstapro.clash.L"] == "<40"
        assert values_by_key["temstapro.clash"] == "2"

    async def test_chain_is_the_suffix_and_the_metric_is_the_same(self, seeded_catalog: AsyncSession) -> None:
        """`temstapro.clash`, `.H` and `.L` are ONE metric measured on different subjects."""
        data = await _query(
            seeded_catalog,
            """
            { candidates { sequenceId scores { key chain metric { columnKey } } } }
            """,
        )

        complex_cand = next(c for c in data["candidates"] if c["sequenceId"] == "complex-cand")
        by_key = _entries_by_key(complex_cand)

        assert by_key["temstapro.clash"]["chain"] is None
        assert by_key["temstapro.clash.H"]["chain"] == "HEAVY"
        assert by_key["temstapro.clash.L"]["chain"] == "LIGHT"
        clash_keys = ("temstapro.clash", "temstapro.clash.H", "temstapro.clash.L")
        assert {by_key[k]["metric"]["columnKey"] for k in clash_keys} == {"clash"}

    async def test_an_uncatalogued_key_has_a_null_metric_not_an_error(self, seeded_catalog: AsyncSession) -> None:
        """ "mystery.column" is in no catalog: it surfaces with metric null, and nothing else breaks."""
        data = await _query(
            seeded_catalog,
            """
            { candidates { sequenceId scores { key value metric { columnKey } } } }
            """,
        )

        complex_cand = next(c for c in data["candidates"] if c["sequenceId"] == "complex-cand")
        mystery = _entries_by_key(complex_cand)["mystery.column"]

        assert mystery["value"] == "1.0"
        assert mystery["metric"] is None


class TestNumericValue:
    """A separate field, not a cast on `value`."""

    async def test_numeric_value_follows_the_catalog_not_the_string(self, seeded_catalog: AsyncSession) -> None:
        """Four keys, four different reasons for the answer.

        temstapro.clash      "2"    INT          -> 2.0
        temstapro.clash.L    "<40"  INT          -> null, does not parse
        temstapro.verdict    "7"    CATEGORICAL  -> null, the catalog says it is not a number
        mystery.column       "1.0"  no metric    -> null, nothing says it is a number
        """
        data = await _query(
            seeded_catalog,
            """
            { candidates { sequenceId scores { key numericValue } } }
            """,
        )

        complex_cand = next(c for c in data["candidates"] if c["sequenceId"] == "complex-cand")
        by_key = _entries_by_key(complex_cand)

        assert by_key["temstapro.clash"]["numericValue"] == 2.0
        assert by_key["temstapro.clash.L"]["numericValue"] is None
        assert by_key["temstapro.verdict"]["numericValue"] is None
        assert by_key["mystery.column"]["numericValue"] is None

    def test_a_parse_failure_logs_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        """The helper alone, no session: a censored value under a numeric metric is a signal."""
        metric = db.Metric(column_key="clash", value_type=db.MetricValueType.INT, module=db.Module(name="temstapro"))

        with caplog.at_level(logging.DEBUG, logger="app.graphql.types"):
            result = _numeric_value("<40", metric)

        assert result is None
        assert any("'<40'" in record.message for record in caplog.records)

    def test_a_bool_that_looks_numeric_stays_null(self) -> None:
        """ "1" under a BOOL metric would float() happily. It must not."""
        metric = db.Metric(column_key="flag", value_type=db.MetricValueType.BOOL, module=db.Module(name="m"))

        assert _numeric_value("1", metric) is None


class TestTheFilters:
    """`scores(module:, chain:, concept:)` — independent, all optional, run in Python."""

    async def test_each_argument_narrows_the_list(self, seeded_catalog: AsyncSession) -> None:
        data = await _query(
            seeded_catalog,
            """
            { candidates { sequenceId
                everything: scores { key }
                boltz: scores(module: "boltz2") { key }
                heavy: scores(chain: HEAVY) { key }
                confidence: scores(concept: "interface_confidence") { key }
                mystery: scores(module: "mystery") { key }
            } }
            """,
        )

        complex_cand = next(c for c in data["candidates"] if c["sequenceId"] == "complex-cand")

        def keys(alias: str) -> list[str]:
            return [entry["key"] for entry in complex_cand[alias]]

        assert len(keys("everything")) == 6
        assert keys("boltz") == ["boltz2.protein_iptm"]
        assert keys("heavy") == ["temstapro.clash.H"]
        assert keys("confidence") == ["boltz2.protein_iptm"], "the complex candidate's ipTM carries the concept"
        assert keys("mystery") == ["mystery.column"], "module matches the key's prefix, so an uncatalogued key is found"

    async def test_concept_drops_the_lone_candidates_iptm(self, seeded_catalog: AsyncSession) -> None:
        """The 'Not an interface' row has no concept, so the lone candidate's ipTM does not match."""
        data = await _query(
            seeded_catalog,
            """
            { candidates { sequenceId confidence: scores(concept: "interface_confidence") { key } } }
            """,
        )

        lone_cand = next(c for c in data["candidates"] if c["sequenceId"] == "lone-cand")

        assert lone_cand["confidence"] == []

    def test_no_arguments_passes_everything(self) -> None:
        assert _passes_filters(decompose("mystery.column"), None, module=None, chain=None, concept=None)

    def test_module_matches_the_key_not_the_catalog(self) -> None:
        """An uncatalogued key (db_metric None) still matches on its own prefix."""
        assert _passes_filters(decompose("mystery.column"), None, module="mystery", chain=None, concept=None)
        assert not _passes_filters(decompose("mystery.column"), None, module="boltz2", chain=None, concept=None)

    def test_concept_needs_a_metric_with_a_concept(self) -> None:
        """Both halves of the None-guard: no metric, and a metric with no concept."""
        no_concept = db.Metric(column_key="clash", module=db.Module(name="temstapro"), concept=None)

        assert not _passes_filters(decompose("mystery.column"), None, module=None, chain=None, concept="x")
        assert not _passes_filters(decompose("temstapro.clash"), no_concept, module=None, chain=None, concept="x")

    def test_chain_null_is_no_filter(self) -> None:
        """Two-valued, deliberately: `chain=None` keeps suffixed and unsuffixed entries alike."""
        assert _passes_filters(decompose("temstapro.clash.H"), None, module=None, chain=None, concept=None)
        assert not _passes_filters(decompose("temstapro.clash.H"), None, module=None, chain=db.ChainRole.LIGHT, concept=None)


class TestTheSdlItself:
    def test_score_entry_and_the_scores_field_are_in_the_schema(self) -> None:
        sdl = str(schema)

        assert "type ScoreEntry {" in sdl
        assert "scores(module: String = null, chain: ChainRole = null, concept: String = null): [ScoreEntry!]!" in sdl
