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

So this file must stay a LEAF — no imports at all, not even from elsewhere in `app.catalog`. It is
the same shape as `variant_kind.py`, for the same reason, and
`tests/test_import_graph.py` is what keeps it that way.
"""

from __future__ import annotations

from typing import NewType

ModuleName = NewType("ModuleName", str)
ColumnKey = NewType("ColumnKey", str)
VariantName = NewType("VariantName", str)
