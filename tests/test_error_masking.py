"""Guard the error-masking policy — no database required.

WHAT COULD GO WRONG
-------------------
Masking fails *open*. If the predicate stops working, or the extension is dropped from the schema,
nothing breaks: queries still succeed, tests still pass, and the only symptom is that error
responses quietly start carrying SQL statement text and internal host:port again. You would find out
by reading a failure response, which is exactly when you are least likely to be looking.

Measured before the extension existed (see `app/graphql/errors.py`), a failing resolver returned:

    (asyncpg.ProgrammingError) column "nonexistent_column" does not exist
    [SQL: SELECT nonexistent_column FROM candidates]

and, with the database down, `Connect call failed ('127.0.0.1', 5999)`.

These tests need no Postgres: the policy is a pure predicate, and the end-to-end cases use a throwaway
schema whose resolvers raise directly.
"""

from __future__ import annotations

import strawberry
from graphql import GraphQLError

from app.graphql.errors import MaskInternalErrors, should_mask_error


class TestShouldMaskError:
    """The predicate: mask unless the error is GraphQL's own, or carries a deliberate `code`.

    EVERY ERROR HERE CARRIES AN `original_error`, and that is not decoration. graphql-core wraps
    whatever a resolver raised, so a resolver-produced error ALWAYS has one by the time the
    extension sees it; a bare `GraphQLError(...)` has none, which is the signature of a syntax or
    validation error instead.

    These tests built them bare until 2026-09-17, with comments saying "what SQLAlchemy, asyncpg and
    any ordinary bug produce" — true of the intent, never of the object. Under the old
    one-condition predicate that made no difference, so they passed while exercising a value that
    cannot occur, and went red the moment `original_error` started to matter. Note which tests did
    NOT go red: `TestMaskingEndToEnd` raises a real `RuntimeError` through a real schema and was
    correct throughout. The end-to-end case was right where the unit case was fictional.
    """

    def test_error_with_a_code_is_shown(self) -> None:
        error = GraphQLError("malformed id", original_error=ValueError("bad"), extensions={"code": "BAD_USER_INPUT"})
        assert should_mask_error(error) is False

    def test_graphqls_own_errors_are_shown(self) -> None:
        """Syntax and validation — the reason the predicate grew a second condition.

        Produced during parse/validate, before any resolver runs, so nothing internal has executed
        and there is nothing to leak. Masking them turned a client's typo into "Internal server
        error.", the opposite of what this file guards.
        """
        assert should_mask_error(GraphQLError("Cannot query field 'nope' on type 'Query'.")) is False

    def test_error_with_no_extensions_is_masked(self) -> None:
        # What SQLAlchemy and asyncpg produce, once graphql-core has wrapped them.
        error = GraphQLError("column does not exist", original_error=RuntimeError("column does not exist"))
        assert should_mask_error(error) is True

    def test_error_with_empty_extensions_is_masked(self) -> None:
        error = GraphQLError("boom", original_error=RuntimeError("boom"), extensions={})
        assert should_mask_error(error) is True

    def test_extensions_without_a_code_key_are_masked(self) -> None:
        # Only `code` counts. Any other metadata an extension might attach must not be read as
        # consent to publish the message.
        error = GraphQLError("boom", original_error=RuntimeError("boom"), extensions={"timestamp": 123})
        assert should_mask_error(error) is True

    def test_an_empty_code_is_masked(self) -> None:
        # Falsy rather than absent — masking is the safe direction, so it fails toward silence.
        error = GraphQLError("boom", original_error=RuntimeError("boom"), extensions={"code": ""})
        assert should_mask_error(error) is True


@strawberry.type
class _Query:
    """A throwaway schema. Neither resolver touches a database."""

    @strawberry.field
    def deliberate(self) -> str | None:
        raise GraphQLError("malformed id: 'haha' is not a UUID", extensions={"code": "BAD_USER_INPUT"})

    @strawberry.field
    def internal(self) -> str | None:
        # Stands in for anything SQLAlchemy raises: the message is detail a client must not see.
        raise RuntimeError('column "nonexistent_column" does not exist [SQL: SELECT ...]')


_schema = strawberry.Schema(query=_Query, extensions=[MaskInternalErrors])


class TestMaskingEndToEnd:
    async def test_deliberate_error_reaches_the_client_intact(self) -> None:
        result = await _schema.execute("{ deliberate }")
        assert result.errors is not None
        assert result.errors[0].message == "malformed id: 'haha' is not a UUID"
        assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}

    async def test_a_client_typo_is_not_masked(self) -> None:
        """End to end, through a real schema: an unknown field must name itself.

        The counterpart to `test_graphqls_own_errors_are_shown`, and the case that was broken in
        production while every unit test above was green.
        """
        result = await _schema.execute("{ nosuchfield }")

        assert result.errors is not None
        assert "nosuchfield" in result.errors[0].message

    async def test_internal_error_is_replaced(self) -> None:
        result = await _schema.execute("{ internal }")
        assert result.errors is not None
        message = result.errors[0].message
        assert message == "Internal server error."
        # The specific things measured as leaking before the extension existed.
        assert "SQL" not in message
        assert "nonexistent_column" not in message

    async def test_the_client_still_gets_both_halves(self) -> None:
        """Masking changes what the client is told, not whether it is told.

        Note `data` is not None: the field is nullable, so the failure is contained to that field
        rather than blanking the whole response. A non-null field would propagate its null upward
        and take the parent object with it — which is a large part of why `ScoreEntry.metric` is
        nullable in the spec.
        """
        result = await _schema.execute("{ internal }")
        assert result.data == {"internal": None}
        assert result.errors is not None and len(result.errors) == 1


def test_the_real_schema_actually_registers_the_extension() -> None:
    """The one that catches the whole thing being switched off.

    Every test above would still pass if `app/graphql/schema.py` dropped `extensions=[...]`, because
    they exercise a throwaway schema. This asserts the policy is wired into the schema that is
    actually served.
    """
    from app.graphql.schema import schema

    assert MaskInternalErrors in schema.extensions
