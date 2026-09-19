"""Stage 5 of docs/scoreentry-plan.md — Mutation.annotateCandidate, the only write in the API.

WRITTEN BEFORE THE CODE. Three decisions under test:

  * The empty string CLEARS (stores NULL). `annotation` is `String!` and the column is nullable.
  * A well-formed id matching nothing is a coded NOT_FOUND, distinct from the BAD_USER_INPUT a
    malformed id already raises. The return type is non-null, so null was never available.
  * The commit goes through `TaskSafeSession`, under the session's lock — the same door every
    statement uses. Proved by holding the lock and watching `commit()` wait.

The `session` fixture turns the mutation's `commit()` into a SAVEPOINT release, so the write is
real for the rest of the test and gone at teardown. That is the machinery tests/conftest.py's
docstring describes, doing what it was built for.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.types import ExecutionResult

from app.graphql.context import Context
from app.graphql.schema import schema
from app.models import orm as db

ANNOTATE = """
mutation ($id: ID!, $annotation: String!) {
  annotateCandidate(id: $id, annotation: $annotation) { id sequenceId annotation }
}
"""


async def _execute(session: AsyncSession, document: str, **variables: Any) -> ExecutionResult:
    """Run one document and hand back the whole result, errors included. For the error tests."""
    return await schema.execute(document, variable_values=variables or None, context_value=Context(session=session))


async def _query(session: AsyncSession, document: str, **variables: Any) -> dict[str, Any]:
    """Run one document, failing loudly on any error. Copied from test_catalog_types.py."""
    result = await _execute(session, document, **variables)
    assert result.errors is None, f"query raised: {[str(e) for e in result.errors]}"
    assert result.data is not None
    return result.data


async def _complex_candidate_id(session: AsyncSession) -> str:
    stmt = select(db.Candidate.id).where(db.Candidate.sequence_id == "complex-cand")
    return str((await session.execute(stmt)).scalar_one())


class TestAnnotateCandidate:
    async def test_the_annotation_is_written_and_read_back(self, seeded_catalog: AsyncSession) -> None:
        candidate_id = await _complex_candidate_id(seeded_catalog)

        written = await _query(seeded_catalog, ANNOTATE, id=candidate_id, annotation="promising binder")
        read_back = await _query(seeded_catalog, "query ($id: ID!) { candidate(id: $id) { annotation } }", id=candidate_id)

        assert written["annotateCandidate"] == {
            "id": candidate_id,
            "sequenceId": "complex-cand",
            "annotation": "promising binder",
        }
        assert read_back["candidate"]["annotation"] == "promising binder"

    async def test_the_empty_string_clears(self, seeded_catalog: AsyncSession) -> None:
        candidate_id = await _complex_candidate_id(seeded_catalog)
        await _query(seeded_catalog, ANNOTATE, id=candidate_id, annotation="to be removed")

        cleared = await _query(seeded_catalog, ANNOTATE, id=candidate_id, annotation="")

        assert cleared["annotateCandidate"]["annotation"] is None

    async def test_a_well_formed_id_matching_nothing_is_not_found(self, seeded_catalog: AsyncSession) -> None:
        result = await _execute(seeded_catalog, ANNOTATE, id=str(uuid.uuid4()), annotation="x")

        assert result.data is None, "non-null root field: the error propagates to the top"
        assert result.errors is not None
        (error,) = result.errors
        assert error.extensions is not None
        assert error.extensions["code"] == "NOT_FOUND"

    async def test_a_nul_character_is_bad_user_input_and_writes_nothing(self, seeded_catalog: AsyncSession) -> None:
        """Postgres `text` cannot store U+0000, so it is refused before the session is touched.

        Found in review of PR #16 and confirmed against the dev database: left to Postgres, the byte
        fails at flush inside commit() as CharacterNotInRepertoireError, which carries no code, so
        the client saw "Internal server error." for its own bad input. The read-back is the half that
        matters most: it shows the refusal came before any write, not after a partial one.
        """
        candidate_id = await _complex_candidate_id(seeded_catalog)
        await _query(seeded_catalog, ANNOTATE, id=candidate_id, annotation="before")

        result = await _execute(seeded_catalog, ANNOTATE, id=candidate_id, annotation="a\x00b")

        assert result.errors is not None
        (error,) = result.errors
        assert error.extensions is not None
        assert error.extensions.get("code") == "BAD_USER_INPUT"
        read_back = await _query(seeded_catalog, "query ($id: ID!) { candidate(id: $id) { annotation } }", id=candidate_id)
        assert read_back["candidate"]["annotation"] == "before", "the refused write left nothing behind"

    async def test_a_malformed_id_is_bad_user_input(self, seeded_catalog: AsyncSession) -> None:
        result = await _execute(seeded_catalog, ANNOTATE, id="not-a-uuid", annotation="x")

        assert result.errors is not None
        (error,) = result.errors
        assert error.extensions is not None
        assert error.extensions["code"] == "BAD_USER_INPUT"


class TestCommitGoesThroughTheLock:
    """`Context.commit()` takes the session's lock, like every statement.

    Timeout-bounded, for the reason tests/test_context_catalog.py::TestTheLocksAreSeparate gives:
    a deadlock hangs a test rather than failing it.
    """

    async def test_commit_completes_when_the_lock_is_free(self, session: AsyncSession) -> None:
        context = Context(session=session)

        await asyncio.wait_for(context.commit(), timeout=10.0)

    async def test_commit_waits_while_the_lock_is_held(self, session: AsyncSession) -> None:
        """Hold the session's lock from outside and watch commit() queue behind it."""
        context = Context(session=session)

        async with context._session._lock:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(context.commit(), timeout=1.0)


class TestTheSdlItself:
    def test_the_mutation_is_in_the_schema(self) -> None:
        sdl = str(schema)

        assert "type Mutation {" in sdl
        assert "annotateCandidate(id: ID!, annotation: String!): Candidate!" in sdl
