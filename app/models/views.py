"""Read-only ORM models mapped onto SQL VIEWS (not tables).

``CandidateSummary`` maps onto the ``candidate_summary`` view (see ``sql/candidate_summary.sql``),
which is created outside Alembic. It is *queried*, never created, inserted, or migrated.

**Why this file is deliberately NOT imported in ``migrations/env.py``:** env.py imports only
``app.models.orm``, so this class is absent from the metadata during ``alembic revision
--autogenerate`` — which stops Alembic from emitting ``CREATE/DROP TABLE`` for what is actually a
view. The ``skip_autogenerate`` info flag below is the belt-and-suspenders version: if the view model
ever *does* get imported into env.py, add an ``include_object`` hook that returns ``False`` for tables
carrying that flag.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from app.database import ModelBase


class CandidateSummary(ModelBase):
    """Read-only mapping onto the ``candidate_summary`` view. Do not insert/update through it.

    A view has no primary key, so we nominate ``candidate_id`` (the full, unique sequence id) as the
    mapper's PK — the ORM requires one, and it's conveniently the value used to locate the PDB file.
    Bare ``Mapped[...]`` annotations become read columns (SQLAlchemy 2.0 infers type + nullability).
    """

    __tablename__ = "candidate_summary"
    __table_args__ = {"info": {"skip_autogenerate": True}}  # it's a VIEW — keep Alembic autogen off it

    candidate_id: Mapped[str] = mapped_column(primary_key=True)
    candidate: Mapped[str]
    experiment: Mapped[str]
    chains: Mapped[str | None]
    n_scores: Mapped[int]
    binding_iptm: Mapped[float | None]
    complex_plddt: Mapped[float | None]
    humanness_oasis: Mapped[float | None]
    humatch_human: Mapped[float | None]
    thermo_class: Mapped[str | None]
    epitope_residues: Mapped[int | None]
