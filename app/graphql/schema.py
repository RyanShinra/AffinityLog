import uuid
from typing import Annotated

import strawberry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.fastapi import GraphQLRouter
from strawberry.types import Info

from app.database import get_db
from app.graphql.types import (
    AnnotateCandidateResult,
    CandidateFilterInput,
    CandidateSortField,
    CandidateType,
    ExperimentSummaryType,
    ExperimentType,
    SortDirection,
    TopCandidateCriteria,
)
from app.models.orm import Candidate, Experiment


async def _get_db_from_context(info: Info) -> AsyncSession:
    return await anext(get_db())


def _orm_candidate_to_type(c: Candidate) -> CandidateType:
    return CandidateType(
        id=c.id,
        experiment_id=c.experiment_id,
        sequence_id=c.sequence_id,
        fasta_sequence=c.fasta_sequence,
        binding_affinity_kd=c.binding_affinity_kd,
        humanness_score=c.humanness_score,
        aggregation_propensity=c.aggregation_propensity,
        annotation=c.annotation,
        raw_scores=c.raw_scores,
        created_at=c.created_at,
    )


def _orm_experiment_to_type(e: Experiment) -> ExperimentType:
    return ExperimentType(
        id=e.id,
        name=e.name,
        recipe_name=e.recipe_name,
        target_name=e.target_name,
        target_pdb_id=e.target_pdb_id,
        source_filename=e.source_filename,
        notes=e.notes,
        created_at=e.created_at,
        candidates=[_orm_candidate_to_type(c) for c in e.candidates],
    )


@strawberry.type
class Query:
    @strawberry.field
    async def experiments(
        self,
        info: Info,
        target_name: str | None = None,
        recipe_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExperimentSummaryType]:
        async with AsyncSession(info.context["engine"]) as db:
            stmt = select(
                Experiment,
                func.count(Candidate.id).label("candidate_count"),
            ).outerjoin(Candidate).group_by(Experiment.id)

            if target_name:
                stmt = stmt.where(Experiment.target_name.ilike(f"%{target_name}%"))
            if recipe_name:
                stmt = stmt.where(Experiment.recipe_name.ilike(f"%{recipe_name}%"))

            stmt = stmt.order_by(Experiment.created_at.desc()).offset(offset).limit(limit)
            rows = (await db.execute(stmt)).all()

            return [
                ExperimentSummaryType(
                    id=exp.id,
                    name=exp.name,
                    recipe_name=exp.recipe_name,
                    target_name=exp.target_name,
                    target_pdb_id=exp.target_pdb_id,
                    source_filename=exp.source_filename,
                    notes=exp.notes,
                    created_at=exp.created_at,
                    candidate_count=count,
                )
                for exp, count in rows
            ]

    @strawberry.field
    async def experiment(self, info: Info, id: uuid.UUID) -> ExperimentType | None:
        from sqlalchemy.orm import selectinload

        async with AsyncSession(info.context["engine"]) as db:
            stmt = (
                select(Experiment)
                .where(Experiment.id == id)
                .options(selectinload(Experiment.candidates))
            )
            result = await db.execute(stmt)
            exp = result.scalar_one_or_none()
            return _orm_experiment_to_type(exp) if exp else None

    @strawberry.field
    async def candidates(
        self,
        info: Info,
        filter: CandidateFilterInput | None = None,
        sort_by: CandidateSortField = CandidateSortField.BINDING_AFFINITY_KD,
        direction: SortDirection = SortDirection.ASC,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CandidateType]:
        async with AsyncSession(info.context["engine"]) as db:
            stmt = select(Candidate)

            if filter:
                if filter.experiment_id is not None:
                    stmt = stmt.where(Candidate.experiment_id == filter.experiment_id)
                if filter.max_binding_affinity_kd is not None:
                    stmt = stmt.where(
                        Candidate.binding_affinity_kd <= filter.max_binding_affinity_kd
                    )
                if filter.min_humanness_score is not None:
                    stmt = stmt.where(Candidate.humanness_score >= filter.min_humanness_score)
                if filter.max_aggregation_propensity is not None:
                    stmt = stmt.where(
                        Candidate.aggregation_propensity <= filter.max_aggregation_propensity
                    )

            sort_col = getattr(Candidate, sort_by.value)
            if direction == SortDirection.DESC:
                stmt = stmt.order_by(sort_col.desc().nulls_last())
            else:
                stmt = stmt.order_by(sort_col.asc().nulls_last())

            stmt = stmt.offset(offset).limit(limit)
            result = await db.execute(stmt)
            return [_orm_candidate_to_type(c) for c in result.scalars().all()]

    @strawberry.field
    async def top_candidates(
        self,
        info: Info,
        experiment_id: uuid.UUID,
        limit: int = 10,
        criteria: TopCandidateCriteria | None = None,
    ) -> list[CandidateType]:
        """
        Rank candidates within an experiment using a composite score.

        Score = w_kd * (1 - norm_kd) + w_hum * norm_hum + w_agg * (1 - norm_agg)

        where each metric is min-max normalized within the experiment so weights
        are comparable regardless of raw scale.
        """
        if criteria is None:
            criteria = TopCandidateCriteria()

        async with AsyncSession(info.context["engine"]) as db:
            result = await db.execute(
                select(Candidate).where(Candidate.experiment_id == experiment_id)
            )
            all_candidates = list(result.scalars().all())

        if not all_candidates:
            return []

        def _minmax(vals: list[float | None]) -> list[float | None]:
            present = [v for v in vals if v is not None]
            if not present or max(present) == min(present):
                return [0.5 if v is not None else None for v in vals]
            lo, hi = min(present), max(present)
            return [(v - lo) / (hi - lo) if v is not None else None for v in vals]

        kd_norm = _minmax([c.binding_affinity_kd for c in all_candidates])
        hum_norm = _minmax([c.humanness_score for c in all_candidates])
        agg_norm = _minmax([c.aggregation_propensity for c in all_candidates])

        def _score(kd: float | None, hum: float | None, agg: float | None) -> float:
            parts: list[float] = []
            if kd is not None:
                parts.append(criteria.weight_binding_affinity * (1 - kd))
            if hum is not None:
                parts.append(criteria.weight_humanness * hum)
            if agg is not None:
                parts.append(criteria.weight_aggregation * (1 - agg))
            return sum(parts) / len(parts) if parts else 0.0

        scored = sorted(
            zip(all_candidates, kd_norm, hum_norm, agg_norm),
            key=lambda t: _score(t[1], t[2], t[3]),
            reverse=True,
        )

        return [_orm_candidate_to_type(c) for c, *_ in scored[:limit]]


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def annotate_candidate(
        self,
        info: Info,
        id: uuid.UUID,
        annotation: str | None,
    ) -> AnnotateCandidateResult | None:
        async with AsyncSession(info.context["engine"]) as db:
            candidate = await db.get(Candidate, id)
            if not candidate:
                return None
            candidate.annotation = annotation
            await db.commit()
            return AnnotateCandidateResult(id=candidate.id, annotation=candidate.annotation)


schema = strawberry.Schema(query=Query, mutation=Mutation)


def get_graphql_router(engine) -> GraphQLRouter:
    async def get_context() -> dict:
        return {"engine": engine}

    return GraphQLRouter(schema, context_getter=get_context)
