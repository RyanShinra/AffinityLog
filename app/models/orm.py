import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipe_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_name: Mapped[str] = mapped_column(String(500), nullable=False)
    target_pdb_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    candidates: Mapped[list["Candidate"]] = relationship(
        "Candidate", back_populates="experiment", cascade="all, delete-orphan"
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    sequence_id: Mapped[str] = mapped_column(String(255), nullable=False)
    fasta_sequence: Mapped[str] = mapped_column(Text, nullable=False)
    # nanomolar; lower = tighter binding
    binding_affinity_kd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # BioPhi-style score 0–1; higher = more human
    humanness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Aggrescan3D-style; lower = lower aggregation risk
    aggregation_propensity: Mapped[float | None] = mapped_column(Float, nullable=True)
    annotation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # All other CSV columns not mapped to a normalized field
    raw_scores: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="candidates")
