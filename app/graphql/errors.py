"""What a GraphQL client is allowed to learn when something goes wrong.

WHY THIS EXISTS
---------------
A resolver that raises does not crash the request. Strawberry catches it, puts ``str(exception)`` in
the response's ``errors`` array, and nulls the field. No traceback is sent — that part is safe. What
is *not* safe is the message, and that was measured rather than assumed:

    { candidate(id: "…") }  with a broken statement
      -> (asyncpg.ProgrammingError) column "nonexistent_column" does not exist
         [SQL: SELECT nonexistent_column FROM candidates]

    ... with the database down
      -> [Errno 111] Connect call failed ('127.0.0.1', 5999)

The full statement text, and the internal host and port. (Credentials do not travel — checked
specifically, with a password in the DSN.) For a portfolio backend, a reviewer finding SQL in an
error response is exactly the sort of thing they are looking for.

WHY IT IS NOT A try/except IN EVERY RESOLVER
--------------------------------------------
An error's *audience* is a boundary concern, not a resolver concern. The resolver knows what went
wrong; only the boundary knows who is asking. Wrapping each ``session.execute`` would put the same
block in six places, each having to invent a return value, and would still miss anything raised
outside those blocks. One extension at the schema boundary sees every error on its way out.

THE POLICY
----------
An error is masked unless one of two things is true. Either it carries a deliberate ``code``
extension — everything raised on purpose sets one (see ``_as_uuid`` in ``schema.py``), and nothing
raised by SQLAlchemy, asyncpg or a plain bug does — or it is GraphQL's OWN error, raised during
parse or validate before any resolver ran, which cannot carry anything internal because nothing
internal has executed. The ``code`` half is the same convention Apollo uses, and needs no new
exception hierarchy: the marker was already there, it just was not being read as one.

The second half was missing until 2026-09-17, and its absence turned every client typo into
"Internal server error." — which is the opposite of what this file is for.

Note that Strawberry's ``default_should_mask_error`` masks **everything**, deliberate errors
included, so the predicate below is required rather than a refinement.

Masking costs no diagnostics: the original error is still logged to the ``strawberry.execution``
logger before this runs. The client loses the detail; the server keeps it.
"""

from __future__ import annotations

from typing import Any

from graphql import GraphQLError
from graphql.error.graphql_error import GraphQLErrorExtensions
from strawberry.extensions import MaskErrors


def should_mask_error(error: GraphQLError) -> bool:
    """True for anything that could carry something internal. Two kinds of error never can.

    GRAPHQL'S OWN ERRORS — syntax and validation — arrive with no `original_error`, because
    graphql-core produced them during parse/validate before any resolver ran. Nothing internal has
    executed, so there is nothing to leak, and a client's typo should read as a typo. Measured:
    `{ nosuchfield }` and an unterminated query both arrive with `original_error is None` and empty
    extensions. Masking them turned every typo into "Internal server error."

    DELIBERATE ERRORS carry a `code` extension — `_as_uuid` in schema.py is the precedent — and the
    code is the marker for "meant to be read". Same convention Apollo uses.

    Everything else is masked, and that default is deliberately conservative. Two mistakes land in
    the same place:

      * a *new* deliberate error that forgets its code entirely
      * one that sets it to "" and never fills it in

    Different mistakes, same remedy. The reverse default, show unless told to hide, would turn
    either into an information leak. Failing toward silence is the cheaper mistake.
    """
    raised_by_a_resolver: bool = error.original_error is not None

    extensions: GraphQLErrorExtensions | None = error.extensions
    code: Any | None = extensions.get("code") if extensions is not None else None
    no_code: bool = code is None  # nobody set one: SQLAlchemy, asyncpg, a plain bug
    blank_code: bool = code == ""  # someone set one and left it empty — an omission, not consent

    return raised_by_a_resolver and (no_code or blank_code)


class MaskInternalErrors(MaskErrors):
    """``MaskErrors`` with this project's policy baked in, so ``schema.py`` just names it.

    A subclass rather than a configured instance because ``strawberry.Schema(extensions=...)`` is
    typed to take ``type[SchemaExtension] | Callable[[], SchemaExtension]`` — a class or a zero-arg
    factory, not an object. Passing an instance happens to work at runtime, but mypy rejects it, and
    the annotation is the honest description of what Strawberry does with it (it instantiates one
    per schema).
    """

    def __init__(self) -> None:
        # The message a client gets in place of anything internal. Deliberately says nothing: not
        # the exception type, not "database", not a correlation id (there is no log aggregation to
        # correlate with yet). Anyone who needs the detail has the server log.
        super().__init__(should_mask_error=should_mask_error, error_message="Internal server error.")
