"""Every native Postgres ENUM must carry exactly the labels its Python class declares.

WHY THIS EXISTS
---------------
`SAEnum` binds the member **name**, not the value, so `VariantKind.INTERFACE` is stored as the
literal `'INTERFACE'`. That makes the Python class and the Postgres type two copies of one
vocabulary, kept in step by hand: adding a member needs an `ALTER TYPE ... ADD VALUE` in its own
transaction, which autogenerate will not write for you (migrations 003 and 005 are the worked
examples).

Nothing checked that they agreed. Measured on 2026-08-24, before this file existed: adding
`VariantKind.SMUGGLED = "smuggled"` with no migration left all 132 tests passing. It is invisible
because the migrations create the type from literal strings, so the test database never consults the
Python class — the new member is simply never written, and an unwritten member cannot fail. The bill
arrives later, as a `DataError` the first time real data uses it.

This matters more since `VariantKind` moved to `app/catalog/`. While it lived in `app/models/orm.py`
its call sites all read `db.VariantKind`, which at least *hinted* at a database obligation. The
obligation did not move; the hint did. A docstring replaced it, and a docstring is what this project
keeps learning not to rely on.

WHAT IT DOES NOT ASSERT: ORDER
------------------------------
Labels are compared as sets. Postgres has its own enum sort order and `ADD VALUE` appends to the
end, so a member inserted mid-list in the Python class legitimately sits last in the database. They
happen to agree today. Asserting that would turn an ordinary future migration into a failure, and a
check that refuses a legitimate shape is worse than no check — see the module docstring of
`test_catalog_invariants.py` for the first version of this project that learned it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import ARRAY, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.type_api import TypeEngine

from app.database import ModelBase
from app.models import orm  # noqa: F401 — importing registers every model in ModelBase.metadata

# `pg_type` holds one row per type; `pg_enum` one row per label. `typtype = 'e'` narrows the first to
# enums, and the namespace join keeps us out of `pg_catalog`'s own types. `enumsortorder` is the
# ordering Postgres itself uses for `ORDER BY` on an enum column — selected so the query is readable
# as the type's real definition, though the comparison below ignores it (see the module docstring).
_LABELS_IN_THE_DATABASE = text("""
    SELECT t.typname AS type_name, e.enumlabel AS label
    FROM pg_type t
    JOIN pg_enum e ON e.enumtypid = t.oid
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE t.typtype = 'e'
      AND n.nspname = 'public'
    ORDER BY t.typname, e.enumsortorder
    """)


def _enum_type_of(sa_type: TypeEngine[object]) -> SAEnum | None:
    """The native ENUM inside a column type, looking through ARRAY.

    `modules.functions` is `ARRAY(Enum(ModuleFunction))` — a plain `isinstance` check on the column
    type walks straight past it. That is not a hypothetical: the first draft of this file did
    exactly that and silently covered six of the seven enums, missing the one with the most members
    and therefore the most room to drift.
    """
    if isinstance(sa_type, ARRAY):
        sa_type = sa_type.item_type
    if isinstance(sa_type, SAEnum) and sa_type.native_enum:
        return sa_type
    return None


def _labels_declared_in_python() -> dict[str, frozenset[str]]:
    """Every native Postgres ENUM the ORM binds, mapped to the labels it expects."""
    declared: dict[str, frozenset[str]] = {}
    for table in ModelBase.metadata.sorted_tables:
        for column in table.columns:
            sa_enum = _enum_type_of(column.type)
            if sa_enum is not None and sa_enum.name:
                declared[sa_enum.name] = frozenset(sa_enum.enums)
    return declared


async def _labels_in_the_database(session: AsyncSession) -> dict[str, frozenset[str]]:
    rows = (await session.execute(_LABELS_IN_THE_DATABASE)).all()
    found: dict[str, set[str]] = {}
    for type_name, label in rows:
        found.setdefault(type_name, set()).add(label)
    return {name: frozenset(labels) for name, labels in found.items()}


class TestTheCollectorSeesEveryEnum:
    """Guards the thing this file's own first draft got wrong.

    If `_labels_declared_in_python` misses a type, every test below still passes — it just stops
    covering it. That is the failure mode a guard must not have, so it gets its own assertions
    rather than being trusted.
    """

    def test_it_looks_through_array_columns(self) -> None:
        assert (
            "modulefunction" in _labels_declared_in_python()
        ), "modules.functions is ARRAY(Enum(ModuleFunction)); the collector must descend into it"

    def test_it_finds_the_enum_on_a_plain_column_too(self) -> None:
        assert "variantkind" in _labels_declared_in_python()


class TestEveryPostgresEnumMatchesItsPythonClass:
    async def test_the_same_enum_types_exist_on_both_sides(self, session: AsyncSession) -> None:
        """Catches an orphan type a migration left behind, and one the ORM expects but nobody created."""
        in_python = set(_labels_declared_in_python())
        in_database = set(await _labels_in_the_database(session))

        assert (
            in_python == in_database
        ), f"only in Python: {sorted(in_python - in_database)}; only in the database: {sorted(in_database - in_python)}"

    @pytest.mark.parametrize("type_name", sorted(_labels_declared_in_python()))
    async def test_the_labels_agree(self, session: AsyncSession, type_name: str) -> None:
        """A member added to the Python class with no `ALTER TYPE ... ADD VALUE` fails here."""
        expected = _labels_declared_in_python()[type_name]
        actual = (await _labels_in_the_database(session)).get(type_name, frozenset())

        missing_from_database = sorted(expected - actual)
        assert not missing_from_database, (
            f"{type_name}: {missing_from_database} declared in Python but absent from the Postgres type. "
            f"Adding a member needs `op.execute(\"ALTER TYPE {type_name} ADD VALUE '...'\")` in its own "
            f"transaction — see migrations 003 and 005."
        )

        unknown_to_python = sorted(actual - expected)
        assert not unknown_to_python, (
            f"{type_name}: {unknown_to_python} exist in the Postgres type but no Python member declares them. "
            f"Postgres cannot DROP an enum value, so removing a member means recreating the type."
        )
