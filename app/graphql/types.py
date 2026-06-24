import enum
import uuid
from datetime import datetime

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class CandidateType:
    id: uuid.UUID
    experiment_id: uuid.UUID
    sequence_id: str
    fasta_sequence: str
    binding_affinity_kd: float | None
    humanness_score: float | None
    aggregation_propensity: float | None
    annotation: str | None
    raw_scores: JSON
    created_at: datetime


@strawberry.type
class ExperimentType:
    id: uuid.UUID
    name: str
    recipe_name: str
    target_name: str
    target_pdb_id: str | None
    source_filename: str | None
    notes: str | None
    created_at: datetime
    candidates: list[CandidateType]


@strawberry.type
class ExperimentSummaryType:
    id: uuid.UUID
    name: str
    recipe_name: str
    target_name: str
    target_pdb_id: str | None
    source_filename: str | None
    notes: str | None
    created_at: datetime
    candidate_count: int


@strawberry.input
class CandidateFilterInput:
    experiment_id: uuid.UUID | None = None
    max_binding_affinity_kd: float | None = None
    min_humanness_score: float | None = None
    max_aggregation_propensity: float | None = None


@strawberry.enum
class CandidateSortField(enum.Enum):
    BINDING_AFFINITY_KD = "binding_affinity_kd"
    HUMANNESS_SCORE = "humanness_score"
    AGGREGATION_PROPENSITY = "aggregation_propensity"
    CREATED_AT = "created_at"


@strawberry.enum
class SortDirection(enum.Enum):
    ASC = "asc"
    DESC = "desc"


@strawberry.input
class TopCandidateCriteria:
    weight_binding_affinity: float = 1.0
    weight_humanness: float = 1.0
    weight_aggregation: float = 1.0


@strawberry.type
class AnnotateCandidateResult:
    id: uuid.UUID
    annotation: str | None
