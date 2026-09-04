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

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog.identifiers import SequenceId
from app.database import ModelBase


class CandidateSummary(ModelBase):
    """Read-only mapping onto the ``candidate_summary`` view. Do not insert/update through it.

    A view has no primary key, so we nominate ``candidate_id`` as the mapper's PK — the ORM requires
    one, and it's conveniently the value used to locate the PDB file. Bare ``Mapped[...]`` annotations
    become read columns (SQLAlchemy 2.0 infers type + nullability).

    ``candidate_id`` IS ``candidates.sequence_id`` (see ``sql/candidate_summary.sql``), and this
    docstring used to call it "the full, unique sequence id". Unique is too strong: ``uq_candidate_seq``
    is the composite ``(experiment_id, sequence_id)``, so the guarantee is per experiment, while this
    view spans all of them. Nominating it as the mapper's PK is still fine — the ORM only needs
    something to identify a row by — but anything building a corpus-wide map keyed on it has to decide
    what two colliding rows mean. ``Context._load_interface_kinds`` is the one that does.
    """

    __tablename__ = "candidate_summary"
    __table_args__ = {"info": {"skip_autogenerate": True}}  # it's a VIEW — keep Alembic autogen off it

    # `String(255)` is given EXPLICITLY, unlike every bare annotation below, and that is required
    # rather than stylistic: a bare `Mapped[SequenceId]` raises
    #   SADeprecationWarning: Matching the provided NewType ... on its resolved value without
    #   matching it in the type_annotation_map is deprecated
    # because SQLAlchemy has to infer the column type from the annotation and a NewType is not in
    # the map. Naming the type means the alias is only ever read as an annotation. Measured, not
    # assumed. The value is inert here — a view is never created from this metadata — but it mirrors
    # `candidates.sequence_id`, which is what this column IS.
    candidate_id: Mapped[SequenceId] = mapped_column(String(255), primary_key=True)
    candidate: Mapped[str]
    experiment: Mapped[str]
    chains: Mapped[str | None]
    antibody_hash: Mapped[str | None]  # H+L fingerprint; equal across rows = the same antibody
    interface_kind: Mapped[str]  # what the ipTM measures: complex / H-L pairing / single chain
    n_scores: Mapped[int]
    iptm: Mapped[float | None]  # was binding_iptm — the name lied on rows with no TARGET chain
    complex_plddt: Mapped[float | None]
    humanness_oasis: Mapped[float | None]
    humatch_human: Mapped[float | None]
    thermo_class: Mapped[str | None]
    epitope_residues: Mapped[int | None]
    epitope_list: Mapped[str | None]  # raw "87;89;90;…" — the router splits it into ints for the 3D viewer
