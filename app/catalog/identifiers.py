"""The three open identifier types: what a string in the catalog vocabulary MEANS.

    ModuleName    "evoprotgrad"              — a Module.name
    ColumnKey     "pseudolikelihood_ratio"   — a Metric.column_key, after the module and any
                                               variant prefix have been split off
    VariantName   "esm"                      — a Metric.variant, the value on whatever axis
                                               `variant_kind` names

All three are `str` at runtime; `NewType` generates no class and costs nothing. What they buy is
that `MetricIdentity` stops being four interchangeable strings — `(column_key, module)` is a
transposition mypy cannot see when both are `str`, and it typechecks, misses the catalog, and
resolves to no metric with no exception and no log.

They are NOT a spelling check. `ModuleName("bolz2")` is a perfectly good ModuleName and matches
nothing. That is what the enums are for, and why the CLOSED sets — `VariantKind`, `ChainRole` —
went the other way. These three are open: a module name is whatever the export header says, and new
ones arrive with new recipes.

WHY THIS IS ITS OWN MODULE AND NOT PART OF `keys.py`
----------------------------------------------------
Because `app/models/orm.py` has to import them, and `keys.py` imports the ORM back — for
`ChainRole`, which `_CHAIN_SUFFIX` is derived from. Putting the aliases in `keys.py` and having the
ORM reach for them there closes a cycle and the whole application stops importing:

    AttributeError: partially initialized module 'app.models.orm' has no attribute 'ChainRole'
    (most likely due to a circular import)

So this file must stay a LEAF — nothing from `app`, not even from elsewhere in `app.catalog`. It is
the same shape as `variant_kind.py`, for the same reason, and `tests/test_import_graph.py` is what
keeps it that way.

WHY NOT JUST `if TYPE_CHECKING:` IN `orm.py`? — IT DOES NOT WORK HERE
--------------------------------------------------------------------
That is the right first question, and normally the right answer: guard the edge where the name is
used only as an annotation, and the cycle dissolves without moving anything. Both edges were tested,
and neither qualifies.

`orm.py` -> these aliases. Annotation-only in the source, but SQLAlchemy RESOLVES ``Mapped[...]``
at class-creation time, so the name must exist at runtime. A guarded import gives::

    sqlalchemy.orm.exc.MappedAnnotationError: Could not resolve all types within mapped
    annotation: "Mapped[ColumnKey]".  Ensure all types are written correctly and are
    imported within the module in use.

``from __future__ import annotations`` does not rescue it — the test above included it.

`keys.py` -> ``orm.ChainRole``. Not annotation-only. ``_CHAIN_SUFFIX`` iterates the enum AT IMPORT
TIME to build the pattern, and ``decompose`` calls ``ChainRole(...)`` per key. Only the
``ScoreKey.chain`` annotation could be guarded — a ``NamedTuple`` stores it as a ``ForwardRef`` and
never resolves it — and that is one of three uses.

With no annotation-only edge to guard, splitting the leaf out is what is left. The alternative would
be moving ``ChainRole`` into ``app/catalog/`` too, which was considered and rejected on its own
merits: it IS the database schema, a native ``chainrole`` type behind ``candidate_chains.role``.
"""

from __future__ import annotations

from typing import NewType

ModuleName = NewType("ModuleName", str)
ColumnKey = NewType("ColumnKey", str)
VariantName = NewType("VariantName", str)

# The vendor's own candidate id — the CSV export's `id` column, landing in `candidates.sequence_id`
# via app/importer/csv_importer.py. Distinct from `candidates.id`, which Postgres generates and no
# export ever sees, and from `antibody_hash`, which is derived and DELIBERATELY collides.
#
# Uniqueness is guaranteed only per experiment (`uq_candidate_seq` is the composite
# `(experiment_id, sequence_id)`), which is why anything keying a corpus-wide map by one has to say
# what it does when two collide. See `Context._load_interface_kinds`.
SequenceId = NewType("SequenceId", str)
