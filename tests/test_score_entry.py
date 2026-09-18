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

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.graphql.context import Context
from app.graphql.schema import schema


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
