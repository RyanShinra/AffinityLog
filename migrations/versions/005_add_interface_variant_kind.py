"""add interface variant kind

Revision ID: 005
Revises: 004
Create Date: 2026-08-01 13:00:06.628123

WHY
---
`boltz2.protein_iptm` is one module, one column, one run — and it still measures three different
physical quantities depending on which chains went into the fold. Measured across the corpus:

    interface_kind                 iptm    protein_iptm   complex_ipde
    antibody-target complex        0.523   0.523           9.522     <- real HER2 binding
    antibody only (H/L pairing)    0.955   0.955           0.468     <- the antibody's own H-L pairing
    single chain (no interface)    0.000   0.000           0.000     <- nothing to score

Exactly those three metrics collapse to 0.000 with no interface; `complex_iplddt` / `complex_plddt`
/ `ptm` stay meaningful (0.906 for a lone chain), so despite the "i" in ipLDDT they are NOT
interface-dependent. The discriminator is empirical, not nominal.

`Metric` identity is (module_id, column_key, variant_kind, variant). Adding INTERFACE lets one
column exist as three catalog rows with three display names and three interpretations, so the API
can say "HER2 binding confidence" or "heavy-light pairing confidence" for the same raw key.

Note the asymmetry this encodes: the catalog row is seeded reference data, but which row APPLIES to
a given candidate is computed at read time by the CASE in the `candidate_summary` view. The catalog
holds the meanings; the view decides which meaning this row has.

KNOWN CEILING (accepted, not solved here): a Metric carries ONE (variant_kind, variant) pair, so
nothing can be both PARAMETER=esm and INTERFACE=complex. No collision exists today — EvoProtGrad's
parameter sweep is sequence-level and Boltz2 has no parameter variants — but a future module that
needs both axes would force a rethink.
"""

from collections.abc import Sequence
from typing import Final

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: str | Sequence[str] | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_NAME: Final[str] = "variantkind"
NEW_VALUE: Final[str] = "INTERFACE"


def upgrade() -> None:
    """Add INTERFACE to the `variantkind` Postgres ENUM.

    Runs as an ordinary statement inside the migration's transaction, which is safe here for two
    reasons worth recording, because both contradict advice you will find elsewhere:

    1. **`ADD VALUE` is transactional from PostgreSQL 12 onward** (this project runs 16). The
       surviving restriction is only that a transaction may not *use* a value it just added — and
       this migration only adds it. The rows that use INTERFACE are written later by the catalog
       seeder, on its own connection.
    2. **Alembic's `autocommit_block()` is NOT available in this project.** It asserts that Alembic
       owns the transaction, but `migrations/env.py` opens one itself via SQLAlchemy
       (`async with connection.begin()`), so `MigrationContext._transaction` is None and the block
       raises `AssertionError` before running any SQL. Reach for it here and it will fail.

    The value is single-quoted because enum labels are string literals to Postgres; unquoted it is a
    syntax error. `IF NOT EXISTS` keeps the migration re-runnable against a database where the label
    was already added by hand.
    """
    op.execute(f"ALTER TYPE {ENUM_NAME} ADD VALUE IF NOT EXISTS '{NEW_VALUE}'")


def downgrade() -> None:
    """Leave INTERFACE in the enum, deliberately.

    PostgreSQL has no `ALTER TYPE ... DROP VALUE`. Removing a label means renaming the type,
    recreating it without the label, migrating every column that uses it, and dropping the old one —
    surgery that fails outright if any surviving row still holds 'INTERFACE'.

    So this downgrade is a no-op with an announcement. The only cost is an unused label on the type
    after downgrading to 004; nothing references it once the seeded metric rows are gone. A
    downgrade whose worst outcome is a spare enum label beats one that can fail halfway through
    rewriting a live column.
    """
    print(f"Downgrading migration {revision} to {down_revision}. {ENUM_NAME}::{NEW_VALUE} is intentionally retained.")
