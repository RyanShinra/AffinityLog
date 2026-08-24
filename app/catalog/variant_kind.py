"""The axis along which one module's repeated column key varies.

`metrics` is unique on `(module_id, column_key, variant_kind, variant)`. Most columns appear once
per module and both are NULL. When a module emits the same column key more than once, meaning
something different each time, `variant_kind` names the AXIS it varies along and `variant` is the
value on that axis:

    PARAMETER     a config knob changed the run       PLM perplexity, model_type=esm  -> variant="esm"
    SOURCE_MODEL  scored against another model        igdesign scRMSD via ABB3        -> variant="ABB3"
    COMPONENT     one readout of a multi-part metric  thermostability Tm1/Tm2/Tm3     -> variant="Tm1"
    MODE          a distinct run-mode of the same module
    TRANSFORM     the raw/"-transformed" card pair    fastdpe SFvCSP                  -> variant="raw"
    INTERFACE     which chains went into the fold     boltz2 protein_iptm             -> variant="antibody-target complex"

WHY THIS LIVES IN `app/catalog/` AND NOT IN `app/models/orm.py`
--------------------------------------------------------------
It is the opposite case to `interface_kind.py`, which is here because it is NOT a database type.
This one IS a live Postgres ENUM, and the ORM still declares the column as `SAEnum(VariantKind)` —
so on storage grounds alone it could sit beside the others.

It is here because storage is not what these members are for. Every rule that READS one is a catalog
rule: `keys.py` decides which kind a key string encodes, `invariants.py` enforces that a heading is
catalogued along exactly one axis, and the two-tier lookup in `app/graphql/context.py` branches on
INTERFACE specifically. The ORM's interest is a single column declaration. Moving it here puts the
vocabulary with the code that reasons about it, and stops `app/catalog/` reaching back through
`app.models.orm` for a value it owns.

The move cost no migration: `SAEnum(VariantKind).name` is `'variantkind'`, derived from the CLASS
name and not the module, so the Postgres type kept its name. Renaming the class WOULD rename the
type. Don't.

ADDING A MEMBER IS A MIGRATION
------------------------------
Because it is a live Postgres ENUM, a new member here is not enough on its own. It needs an
`op.execute("ALTER TYPE variantkind ADD VALUE '...'")` in its own transaction, separate from the
migration's other DDL — pre-PG12 you cannot ADD VALUE and then use the new value in the same
transaction, and autogenerate will not emit it for you. Migrations 003 (TRANSFORM) and 005
(INTERFACE) are the two worked examples.
"""

from __future__ import annotations

import enum


class VariantKind(enum.Enum):
    PARAMETER = "parameter"
    MODE = "mode"
    SOURCE_MODEL = "source_model"
    COMPONENT = "component"
    # For the raw vs. "-transformed" card pairs found in the Module Evaluation scrape
    # (see bio-discovery-scrape-handoff.md §6/§7
    # — e.g. FastDPE's SFvCSP column has both a raw and a "-transformed" Metric).
    # Added by migration 003. Imported from the scrape's `module_output` "-transformed" suffix as
    # variant_kind=TRANSFORM, variant="transformed" — the existing UniqueConstraint(module_id,
    # column_key, variant_kind, variant) already handles the identity correctly.
    TRANSFORM = "transform"
    # Added in migration 005. The discriminator is NOT a property of the metric definition — it is
    # the shape of the candidate the value was computed on, i.e. which chains went into the fold.
    # `boltz2.protein_iptm` measures HER2 binding (0.196-0.793) on an antibody-target complex, the
    # antibody's own heavy-light pairing (~0.95) with no antigen present, and nothing at all (0.000)
    # on a lone chain — one column, three quantities. The variant carries the meaning; the CASE in
    # the candidate_summary view decides which one a given row has.
    # Only metrics whose value COLLAPSES TO 0.000 without an interface use this (iptm,
    # protein_iptm, complex_ipde); complex_iplddt/complex_plddt/ptm stay meaningful for a single
    # chain, so despite the "i" in ipLDDT they are not interface-dependent.
    INTERFACE = "interface"
