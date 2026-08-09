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
    """The predicate: safe to show iff the error carries a deliberate `code`."""

    def test_error_with_a_code_is_shown(self) -> None:
        error = GraphQLError("malformed id", extensions={"code": "BAD_USER_INPUT"})
        assert should_mask_error(error) is False

    def test_error_with_no_extensions_is_masked(self) -> None:
        # What SQLAlchemy, asyncpg and any ordinary bug produce.
        assert should_mask_error(GraphQLError("column does not exist")) is True

    def test_error_with_empty_extensions_is_masked(self) -> None:
        assert should_mask_error(GraphQLError("boom", extensions={})) is True

    def test_extensions_without_a_code_key_are_masked(self) -> None:
        # Only `code` counts. Any other metadata an extension might attach must not be read as
        # consent to publish the message.
        assert should_mask_error(GraphQLError("boom", extensions={"timestamp": 123})) is True

    def test_an_empty_code_is_masked(self) -> None:
        # Falsy rather than absent — masking is the safe direction, so it fails toward silence.
        assert should_mask_error(GraphQLError("boom", extensions={"code": ""})) is True


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
